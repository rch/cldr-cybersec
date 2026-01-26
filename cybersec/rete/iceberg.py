"""
Iceberg table optimization using RETE rules.

This module provides intelligent optimization recommendations for Apache Iceberg
tables based on table statistics, query patterns, and best practices.

Key optimizations:
- **Hidden Partitioning**: Leverage Iceberg's automatic partition pruning
- **Partition Pruning**: Ensure queries filter on partition columns first
- **Compaction**: Optimize file sizes and reduce small files
- **Manifest Optimization**: Rewrite manifests for better metadata performance
- **Snapshot Management**: Expire old snapshots to reduce metadata overhead
- **Sort Order**: Optimize data layout for query patterns
"""

from dataclasses import dataclass
from typing import Any

from .engine import ReteEngine, Activation
from .facts import Fact, FactType, table_fact, partition_fact, query_pattern_fact
from .rules import Rule, Condition, Action, when, then


@dataclass
class TableStats:
    """Statistics for an Iceberg table."""

    table_name: str
    namespace: str = "default"

    # Size metrics
    row_count: int = 0
    file_count: int = 0
    total_size_mb: int = 0

    # Partition metrics
    partition_count: int = 0
    partition_spec: str = ""  # e.g., "days(event_timestamp)"

    # File metrics
    avg_file_size_mb: int = 0
    min_file_size_mb: int = 0
    max_file_size_mb: int = 0

    # Snapshot metrics
    snapshot_count: int = 1
    days_since_compaction: int = 0
    orphan_file_count: int = 0

    # Sort order
    has_sort_order: bool = False
    sort_columns: list[str] | None = None

    # Query patterns (if available)
    filters_on_partition: bool = False
    filters_on_timestamp: bool = False
    uses_time_range: bool = False
    avg_rows_scanned: int = 0
    avg_files_scanned: int = 0

    def to_fact(self) -> Fact:
        """Convert to a RETE fact."""
        return table_fact(
            self.table_name,
            row_count=self.row_count,
            partition_count=self.partition_count,
            file_count=self.file_count,
            total_size_mb=self.total_size_mb,
            avg_file_size_mb=self.avg_file_size_mb,
            snapshot_count=self.snapshot_count,
            days_since_compaction=self.days_since_compaction,
            has_sort_order=self.has_sort_order,
            partition_spec=self.partition_spec,
        )


@dataclass
class OptimizationRecommendation:
    """A recommended optimization action."""

    action_type: str
    table_name: str
    priority: int
    description: str
    command: str  # SQL or PyIceberg command
    estimated_impact: str
    params: dict[str, Any] | None = None


# --- Iceberg Optimization Rules ---

# File size thresholds (in MB)
MIN_OPTIMAL_FILE_SIZE = 32  # Files smaller than this need compaction
MAX_OPTIMAL_FILE_SIZE = 512  # Files larger than this may need splitting
TARGET_FILE_SIZE = 128  # Ideal file size

# Snapshot thresholds
MAX_SNAPSHOTS_BEFORE_EXPIRE = 50
SNAPSHOT_EXPIRE_DAYS = 7

# Partition thresholds
MAX_FILES_PER_PARTITION = 20
MIN_ROWS_FOR_PARTITIONING = 100_000

# Streaming detection thresholds
STREAMING_SNAPSHOT_RATIO = 100  # snapshot_count / rows * 100000 - if >100, likely streaming

# Heuristics captured from analysis
OPTIMIZATION_HEURISTICS = {
    # File size thresholds
    "min_file_size_mb": MIN_OPTIMAL_FILE_SIZE,
    "target_file_size_mb": TARGET_FILE_SIZE,
    "max_file_size_mb": MAX_OPTIMAL_FILE_SIZE,
    # File count thresholds
    "files_per_partition_max": MAX_FILES_PER_PARTITION,
    "files_per_partition_target": 5,
    # Snapshot thresholds
    "max_snapshots": MAX_SNAPSHOTS_BEFORE_EXPIRE,
    "snapshot_retention_hours": 24,  # For streaming tables
    "snapshot_retention_days": SNAPSHOT_EXPIRE_DAYS,
    # Partition heuristics
    "partition_row_count_min": 10_000,
    "partition_row_count_target": 100_000,
    "partition_row_count_max": 1_000_000,
    # Query pattern thresholds
    "full_scan_row_threshold": 100_000,
    "scan_efficiency_threshold": 0.1,
}


ICEBERG_RULES = [
    # --- Compaction Rules ---
    Rule(
        "compact_small_files",
        conditions=when(
            ("table.*.avg_file_size_mb", "<=", MIN_OPTIMAL_FILE_SIZE),
            ("table.*.file_count", ">", 10),
        ),
        actions=then(Action.compact("{table}")),
        priority=800,
        category="compaction",
        description="Compact tables with average file size below 32MB to improve query performance",
    ),
    Rule(
        "compact_many_files_per_partition",
        conditions=when(
            ("table.*.files_per_partition", ">", MAX_FILES_PER_PARTITION),
        ),
        actions=then(Action.compact("{table}")),
        priority=750,
        category="compaction",
        description="Compact partitions with too many small files (>20 files per partition)",
    ),
    Rule(
        "compact_after_many_writes",
        conditions=when(
            ("table.*.days_since_compaction", ">", 7),
            ("table.*.file_count", ">", 50),
        ),
        actions=then(Action.compact("{table}")),
        priority=600,
        category="compaction",
        description="Schedule compaction for tables not compacted in 7+ days with many files",
    ),
    # --- Manifest Optimization Rules ---
    Rule(
        "rewrite_manifests_many_files",
        conditions=when(
            ("table.*.file_count", ">", 1000),
            ("table.*.avg_file_size_mb", ">=", MIN_OPTIMAL_FILE_SIZE),  # Already compacted
        ),
        actions=then(Action.rewrite_manifests("{table}")),
        priority=500,
        category="metadata",
        description="Rewrite manifests for large tables to optimize metadata operations",
    ),
    # --- Snapshot Management Rules ---
    Rule(
        "expire_old_snapshots",
        conditions=when(
            ("table.*.snapshot_count", ">", MAX_SNAPSHOTS_BEFORE_EXPIRE),
        ),
        actions=then(Action.expire_snapshots("{table}", older_than_days=SNAPSHOT_EXPIRE_DAYS)),
        priority=400,
        category="maintenance",
        description="Expire snapshots older than 7 days when snapshot count exceeds 50",
    ),
    # --- Partition Evolution Rules ---
    Rule(
        "suggest_hourly_partitioning",
        conditions=when(
            ("table.*.row_count", ">", 10_000_000),  # 10M+ rows
            ("query.*.uses_time_range", "==", True),
            ("table.*.partition_spec", "==", ""),  # Unpartitioned
        ),
        actions=then(Action.evolve_partition("{table}", "hours(event_timestamp)")),
        priority=300,
        category="partitioning",
        description="Suggest hourly partitioning for large tables with time-range queries",
    ),
    Rule(
        "suggest_daily_partitioning",
        conditions=when(
            ("table.*.row_count", ">", 1_000_000),  # 1M+ rows
            ("table.*.row_count", "<=", 10_000_000),
            ("query.*.uses_time_range", "==", True),
            ("table.*.partition_spec", "==", ""),
        ),
        actions=then(Action.evolve_partition("{table}", "days(event_timestamp)")),
        priority=290,
        category="partitioning",
        description="Suggest daily partitioning for medium tables with time-range queries",
    ),
    # --- Query Optimization Alerts ---
    Rule(
        "alert_full_table_scan",
        conditions=when(
            ("query.*.is_full_scan", "==", True),
            ("table.*.row_count", ">", 100_000),
        ),
        actions=then(
            Action.alert(
                "Full table scan detected on {table}. Add partition filters to queries.",
                severity="warning",
            )
        ),
        priority=900,
        category="query",
        description="Alert when queries perform full table scans on large tables",
    ),
    Rule(
        "alert_no_partition_pruning",
        conditions=when(
            ("query.*.has_partition_pruning", "==", False),
            ("table.*.partition_count", ">", 10),
        ),
        actions=then(
            Action.alert(
                "Query on {table} not using partition pruning. Filter on partition column first.",
                severity="warning",
            )
        ),
        priority=850,
        category="query",
        description="Alert when queries don't leverage hidden partitioning",
    ),
    # --- Sort Order Optimization ---
    Rule(
        "suggest_sort_order",
        conditions=when(
            ("table.*.has_sort_order", "==", False),
            ("table.*.row_count", ">", 1_000_000),
            ("query.*.filters_on_timestamp", "==", True),
        ),
        actions=then(
            Action.alert(
                "Consider adding sort order on event_timestamp for {table} to improve range queries",
                severity="info",
            )
        ),
        priority=200,
        category="optimization",
        description="Suggest sort order for tables with timestamp-based queries",
    ),
    # --- Streaming Pattern Detection ---
    Rule(
        "detect_streaming_write_pattern",
        conditions=when(
            ("table.*.snapshot_count", ">", 100),
            ("table.*.file_count", ">", 100),
            # High snapshot-to-row ratio indicates streaming
        ),
        actions=then(
            Action.alert(
                "Streaming write pattern detected on {table}. Consider batch commits or snapshot coalescing.",
                severity="warning",
            )
        ),
        priority=700,
        category="maintenance",
        description="Detect streaming write pattern causing snapshot/file explosion",
    ),
    # --- Small Table Partitioning (lower threshold) ---
    Rule(
        "suggest_partitioning_small_table",
        conditions=when(
            ("table.*.row_count", ">", 100_000),  # Lower threshold: 100K
            ("table.*.row_count", "<=", 1_000_000),
            ("query.*.uses_time_range", "==", True),
            ("table.*.partition_spec", "==", ""),
        ),
        actions=then(
            Action.alert(
                "Consider adding time partitioning to {table} for better query performance on time-range filters.",
                severity="info",
            )
        ),
        priority=280,
        category="partitioning",
        description="Suggest partitioning for smaller tables with time-range queries",
    ),
    # --- Extreme File Fragmentation ---
    Rule(
        "alert_extreme_fragmentation",
        conditions=when(
            ("table.*.file_count", ">", 1000),
            ("table.*.avg_file_size_mb", "==", 0),  # Files < 1MB avg
        ),
        actions=then(
            Action.alert(
                "CRITICAL: Extreme file fragmentation on {table}. Files averaging < 1MB. Immediate compaction required.",
                severity="critical",
            )
        ),
        priority=950,
        category="compaction",
        description="Alert on extreme file fragmentation (thousands of tiny files)",
    ),
    # --- Snapshot Explosion (critical) ---
    Rule(
        "alert_snapshot_explosion",
        conditions=when(
            ("table.*.snapshot_count", ">", 1000),
        ),
        actions=then(
            Action.alert(
                "CRITICAL: Snapshot explosion on {table}. {snapshot_count} snapshots causing metadata bloat. Expire immediately.",
                severity="critical",
            )
        ),
        priority=920,
        category="maintenance",
        description="Alert on excessive snapshot accumulation",
    ),
]


class IcebergOptimizer:
    """
    Iceberg table optimizer using RETE rules.

    Example:
        optimizer = IcebergOptimizer()

        # Add table statistics
        optimizer.add_table(TableStats(
            table_name="cloudtrail",
            row_count=10_000_000,
            file_count=500,
            total_size_mb=2048,
            avg_file_size_mb=4,  # Small files!
            partition_count=30,
        ))

        # Add query pattern info (optional)
        optimizer.add_query_pattern(
            "cloudtrail",
            filters_on_timestamp=True,
            uses_time_range=True,
        )

        # Get recommendations
        recommendations = optimizer.analyze()
        for rec in recommendations:
            print(f"{rec.action_type}: {rec.description}")
            print(f"  Command: {rec.command}")
    """

    def __init__(self, custom_rules: list[Rule] | None = None):
        self.engine = ReteEngine()

        # Add default rules
        for rule in ICEBERG_RULES:
            self.engine.add_rule(rule)

        # Add custom rules
        if custom_rules:
            for rule in custom_rules:
                self.engine.add_rule(rule)

    def add_table(self, stats: TableStats) -> None:
        """Add table statistics to the optimizer."""
        self.engine.assert_fact(stats.to_fact())

    def add_query_pattern(
        self,
        table_name: str,
        *,
        filters_on_partition: bool = False,
        filters_on_timestamp: bool = False,
        selects_all_columns: bool = False,
        uses_time_range: bool = False,
        avg_rows_scanned: int = 0,
        avg_files_scanned: int = 0,
    ) -> None:
        """Add query pattern information for a table."""
        fact = query_pattern_fact(
            table_name,
            filters_on_partition=filters_on_partition,
            filters_on_timestamp=filters_on_timestamp,
            selects_all_columns=selects_all_columns,
            uses_time_range=uses_time_range,
            avg_rows_scanned=avg_rows_scanned,
            avg_files_scanned=avg_files_scanned,
        )
        self.engine.assert_fact(fact)

    def analyze(self) -> list[OptimizationRecommendation]:
        """
        Analyze tables and return optimization recommendations.

        Returns recommendations sorted by priority (highest first).
        """
        result = self.engine.solve()

        if not result.success:
            return []

        recommendations = []
        for activation in result.activations:
            for action in activation.actions:
                rec = self._action_to_recommendation(action, activation)
                if rec:
                    recommendations.append(rec)

        return recommendations

    def _action_to_recommendation(
        self, action: Action, activation: Activation
    ) -> OptimizationRecommendation | None:
        """Convert an action to a recommendation with SQL/PyIceberg command."""
        table_name = action.target

        if action.action_type == "compact":
            return OptimizationRecommendation(
                action_type="compaction",
                table_name=table_name,
                priority=activation.priority,
                description=activation.rule.description,
                command=self._compaction_command(table_name),
                estimated_impact="Reduce file count, improve query performance",
                params=action.params,
            )

        elif action.action_type == "rewrite_manifests":
            return OptimizationRecommendation(
                action_type="rewrite_manifests",
                table_name=table_name,
                priority=activation.priority,
                description=activation.rule.description,
                command=self._rewrite_manifests_command(table_name),
                estimated_impact="Faster metadata operations and query planning",
            )

        elif action.action_type == "expire_snapshots":
            older_than_days = action.params.get("older_than_days", 7)
            return OptimizationRecommendation(
                action_type="expire_snapshots",
                table_name=table_name,
                priority=activation.priority,
                description=activation.rule.description,
                command=self._expire_snapshots_command(table_name, older_than_days),
                estimated_impact="Reduce metadata size and storage costs",
                params=action.params,
            )

        elif action.action_type == "evolve_partition":
            new_spec = action.params.get("new_spec", "")
            return OptimizationRecommendation(
                action_type="partition_evolution",
                table_name=table_name,
                priority=activation.priority,
                description=activation.rule.description,
                command=self._evolve_partition_command(table_name, new_spec),
                estimated_impact="Enable partition pruning for time-range queries",
                params=action.params,
            )

        elif action.action_type == "alert":
            severity = action.params.get("severity", "warning")
            return OptimizationRecommendation(
                action_type="alert",
                table_name=table_name,
                priority=activation.priority,
                description=action.target,  # Alert message is in target
                command="",  # No command for alerts
                estimated_impact=f"[{severity.upper()}] Review query patterns",
                params=action.params,
            )

        return None

    def _compaction_command(self, table_name: str) -> str:
        """Generate compaction command."""
        return f"""# PyIceberg compaction
from pyiceberg.catalog import load_catalog
catalog = load_catalog("default")
table = catalog.load_table("{table_name}")
table.rewrite_data_files(
    target_file_size_in_bytes={TARGET_FILE_SIZE * 1024 * 1024},
    strategy="binpack"
)

# Or via Spark SQL:
# CALL system.rewrite_data_files(table => '{table_name}', options => map('target-file-size-bytes', '{TARGET_FILE_SIZE * 1024 * 1024}'))
"""

    def _rewrite_manifests_command(self, table_name: str) -> str:
        """Generate manifest rewrite command."""
        return f"""# PyIceberg manifest rewrite
from pyiceberg.catalog import load_catalog
catalog = load_catalog("default")
table = catalog.load_table("{table_name}")
table.rewrite_manifests()

# Or via Spark SQL:
# CALL system.rewrite_manifests(table => '{table_name}')
"""

    def _expire_snapshots_command(self, table_name: str, older_than_days: int) -> str:
        """Generate snapshot expiration command."""
        return f"""# PyIceberg snapshot expiration
from pyiceberg.catalog import load_catalog
from datetime import datetime, timedelta

catalog = load_catalog("default")
table = catalog.load_table("{table_name}")
table.expire_snapshots(
    older_than=datetime.now() - timedelta(days={older_than_days}),
    retain_last=5
)

# Or via Spark SQL:
# CALL system.expire_snapshots(table => '{table_name}', older_than => TIMESTAMP '{older_than_days} days ago', retain_last => 5)
"""

    def _evolve_partition_command(self, table_name: str, new_spec: str) -> str:
        """Generate partition evolution command."""
        return f"""# PyIceberg partition evolution
from pyiceberg.catalog import load_catalog
from pyiceberg.transforms import DayTransform, HourTransform

catalog = load_catalog("default")
table = catalog.load_table("{table_name}")

# Add new partition field (hidden partitioning)
with table.update_spec() as update:
    update.add_field("{new_spec}")

# Note: Existing data remains unchanged. New data will use the new partition spec.
# Iceberg's hidden partitioning automatically applies pruning based on
# event_timestamp filters, even though queries don't reference partition columns.

# Or via Spark SQL:
# ALTER TABLE {table_name} ADD PARTITION FIELD {new_spec}
"""

    def explain_rule(self, rule_id: str) -> dict[str, Any]:
        """Explain why a specific rule did or didn't fire."""
        return self.engine.explain(rule_id)

    def get_table_facts(self, table_name: str) -> dict[str, Any]:
        """Get all facts for a specific table."""
        facts = {}
        for key, fact in self.engine.facts.items():
            if table_name in key:
                facts[key] = {
                    "type": fact.fact_type,
                    "id": fact.fact_id,
                    "attributes": fact.attributes,
                }
        return facts
