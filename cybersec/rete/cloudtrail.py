"""
CloudTrail-specific schema optimization rules.

This module provides RETE rules tailored for CloudTrail data at production scale,
considering real-world security investigation patterns:

1. **Time-bounded investigations** - "What happened between 2-3pm yesterday?"
2. **User-centric queries** - "What did user X do in the last 24h?"
3. **Resource forensics** - "Who accessed S3 bucket Y?"
4. **IP-based hunting** - "What activity came from IP X?"
5. **Error/anomaly detection** - "Show me all failed API calls"

Schema Design Principles:
- Partition by TIME first (hour for high-volume, day for moderate)
- Consider composite partitions: (day, eventSource) or (day, awsRegion)
- Denormalize frequently-filtered fields from JSON
- Z-order/sort by (sourceIPAddress, userIdentity.arn) for forensics
- Keep raw JSON for flexibility, but extract hot fields

Production Scale Considerations:
- 10M-100M events/day typical for enterprise
- 90% of queries are time-bounded (last 24h, last 7d)
- 80% of queries filter on: time, user, eventSource, errorCode
- Hot data: last 7 days, Warm: 7-30 days, Cold: 30+ days
"""

from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from .engine import ReteEngine
from .facts import Fact, FactType
from .rules import Rule, Condition, Action, when, then
from .backward import BackwardChainer, Goal, ProofStatus


class QueryPattern(Enum):
    """Common CloudTrail query patterns."""
    TIME_BOUNDED = "time_bounded"           # Last N hours/days
    USER_INVESTIGATION = "user_investigation"  # What did user X do?
    RESOURCE_FORENSICS = "resource_forensics"  # Who accessed resource Y?
    IP_HUNTING = "ip_hunting"               # Activity from IP X
    ERROR_DETECTION = "error_detection"     # Failed API calls
    COMPLIANCE_AUDIT = "compliance_audit"   # All actions on sensitive resources


class DataTemperature(Enum):
    """Data access temperature for tiered storage."""
    HOT = "hot"      # Last 7 days - fast SSD, no compression trade-off
    WARM = "warm"    # 7-30 days - balanced
    COLD = "cold"    # 30+ days - max compression, archive storage


@dataclass
class CloudTrailTableStats:
    """Statistics specific to CloudTrail tables."""

    table_name: str
    namespace: str = "default"

    # Volume metrics
    events_per_day: int = 0
    total_events: int = 0
    total_size_gb: float = 0
    days_of_data: int = 1

    # Current schema
    is_partitioned: bool = False
    partition_columns: list[str] = field(default_factory=list)
    has_time_partition: bool = False
    partition_granularity: str = ""  # "hour", "day", "month"

    # Denormalization state
    denormalized_fields: list[str] = field(default_factory=list)
    has_json_blob: bool = True  # event_data column

    # Sort/clustering
    sort_columns: list[str] = field(default_factory=list)
    z_order_columns: list[str] = field(default_factory=list)

    # File metrics
    file_count: int = 0
    avg_file_size_mb: float = 0
    snapshot_count: int = 0

    # Query pattern observations
    observed_patterns: list[QueryPattern] = field(default_factory=list)
    pct_queries_time_filtered: float = 90.0  # Default assumption
    pct_queries_need_json_parse: float = 80.0
    avg_query_time_range_hours: float = 24.0

    # Cardinality observations
    unique_users: int = 0
    unique_ips: int = 0
    unique_event_sources: int = 0
    unique_regions: int = 0
    error_rate: float = 0.0  # % of events with errorCode

    def estimated_events_per_hour(self) -> int:
        return self.events_per_day // 24 if self.events_per_day else 0

    def needs_hourly_partition(self) -> bool:
        """Hourly partitioning recommended for >1M events/day."""
        return self.events_per_day > 1_000_000

    def needs_daily_partition(self) -> bool:
        """Daily partitioning recommended for 100K-1M events/day."""
        return 100_000 < self.events_per_day <= 1_000_000

    def to_facts(self) -> list[Fact]:
        """Convert to RETE facts for rule evaluation."""
        facts = []

        # Table-level fact
        facts.append(Fact(
            "cloudtrail_table", self.table_name,
            events_per_day=self.events_per_day,
            total_events=self.total_events,
            total_size_gb=self.total_size_gb,
            days_of_data=self.days_of_data,
            is_partitioned=self.is_partitioned,
            has_time_partition=self.has_time_partition,
            partition_granularity=self.partition_granularity,
            has_json_blob=self.has_json_blob,
            file_count=self.file_count,
            avg_file_size_mb=self.avg_file_size_mb,
            snapshot_count=self.snapshot_count,
            pct_queries_time_filtered=self.pct_queries_time_filtered,
            pct_queries_need_json_parse=self.pct_queries_need_json_parse,
            unique_users=self.unique_users,
            unique_ips=self.unique_ips,
            unique_event_sources=self.unique_event_sources,
            error_rate=self.error_rate,
        ))

        # Schema state facts for what-if analysis
        facts.append(Fact(
            "schema_state", self.table_name,
            has_time_partition=self.has_time_partition,
            has_user_column=("userIdentity.arn" in self.denormalized_fields),
            has_ip_column=("sourceIPAddress" in self.denormalized_fields),
            has_error_column=("errorCode" in self.denormalized_fields),
            has_event_source_column=("eventSource" in self.denormalized_fields),
            has_region_column=("awsRegion" in self.denormalized_fields),
            is_sorted=len(self.sort_columns) > 0,
            is_z_ordered=len(self.z_order_columns) > 0,
        ))

        return facts


# === CloudTrail-Specific RETE Rules ===

CLOUDTRAIL_RULES = [
    # --- Partitioning Rules ---
    Rule(
        "ct_require_time_partition",
        conditions=when(
            ("cloudtrail_table.*.events_per_day", ">", 10000),
            ("cloudtrail_table.*.has_time_partition", "==", False),
        ),
        actions=then(
            Action.alert(
                "CloudTrail table {table} MUST have time-based partitioning. "
                "90%+ of security queries filter by time.",
                severity="critical",
            )
        ),
        priority=1000,
        category="partitioning",
        description="Time partitioning is essential for CloudTrail query performance",
    ),

    Rule(
        "ct_suggest_hourly_partition",
        conditions=when(
            ("cloudtrail_table.*.events_per_day", ">", 1_000_000),
            ("cloudtrail_table.*.has_time_partition", "==", False),
        ),
        actions=then(
            Action.evolve_partition("{table}", "hours(event_time)")
        ),
        priority=950,
        category="partitioning",
        description="Recommend hourly partitioning for high-volume CloudTrail",
    ),

    Rule(
        "ct_suggest_daily_partition",
        conditions=when(
            ("cloudtrail_table.*.events_per_day", ">", 100_000),
            ("cloudtrail_table.*.events_per_day", "<=", 1_000_000),
            ("cloudtrail_table.*.has_time_partition", "==", False),
        ),
        actions=then(
            Action.evolve_partition("{table}", "days(event_time)")
        ),
        priority=940,
        category="partitioning",
        description="Recommend daily partitioning for moderate-volume CloudTrail",
    ),

    Rule(
        "ct_consider_composite_partition",
        conditions=when(
            ("cloudtrail_table.*.events_per_day", ">", 10_000_000),
            ("cloudtrail_table.*.unique_event_sources", "<=", 10),
        ),
        actions=then(
            Action.alert(
                "Consider composite partitioning (day, eventSource) for {table}. "
                "With {unique_event_sources} services and high volume, this enables "
                "efficient service-specific queries while maintaining time locality.",
                severity="info",
            )
        ),
        priority=850,
        category="partitioning",
        description="Suggest composite partitioning for very high volume with low service cardinality",
    ),

    # --- Schema Denormalization Rules ---
    Rule(
        "ct_denormalize_hot_fields",
        conditions=when(
            ("cloudtrail_table.*.has_json_blob", "==", True),
            ("cloudtrail_table.*.pct_queries_need_json_parse", ">", 50),
            ("cloudtrail_table.*.events_per_day", ">", 100_000),
        ),
        actions=then(
            Action.alert(
                "Denormalize frequently-queried fields from event_data JSON in {table}. "
                "Recommended columns: event_name, event_source, aws_region, user_arn, "
                "source_ip, error_code. This eliminates JSON parsing for 80%+ of queries.",
                severity="warning",
            )
        ),
        priority=900,
        category="schema",
        description="Recommend denormalizing hot fields from JSON blob",
    ),

    Rule(
        "ct_add_error_column",
        conditions=when(
            ("cloudtrail_table.*.error_rate", ">", 5),
            ("schema_state.*.has_error_column", "==", False),
        ),
        actions=then(
            Action.alert(
                "Add errorCode column to {table}. With {error_rate}% error rate, "
                "error detection queries will benefit significantly from direct column access.",
                severity="info",
            )
        ),
        priority=800,
        category="schema",
        description="Suggest errorCode column when error rate is significant",
    ),

    Rule(
        "ct_add_user_column",
        conditions=when(
            ("cloudtrail_table.*.unique_users", ">", 10),
            ("schema_state.*.has_user_column", "==", False),
            ("cloudtrail_table.*.events_per_day", ">", 50_000),
        ),
        actions=then(
            Action.alert(
                "Add user_arn column (extracted from userIdentity.arn) to {table}. "
                "User-centric investigations are a primary security use case.",
                severity="info",
            )
        ),
        priority=790,
        category="schema",
        description="Suggest user column for user-centric investigations",
    ),

    Rule(
        "ct_add_ip_column",
        conditions=when(
            ("cloudtrail_table.*.unique_ips", ">", 100),
            ("schema_state.*.has_ip_column", "==", False),
        ),
        actions=then(
            Action.alert(
                "Add source_ip column to {table}. IP-based threat hunting requires "
                "fast filtering by source IP address.",
                severity="info",
            )
        ),
        priority=780,
        category="schema",
        description="Suggest IP column for threat hunting",
    ),

    # --- Sort Order Rules ---
    Rule(
        "ct_add_sort_order",
        conditions=when(
            ("cloudtrail_table.*.has_time_partition", "==", True),
            ("schema_state.*.is_sorted", "==", False),
            ("cloudtrail_table.*.events_per_day", ">", 500_000),
        ),
        actions=then(
            Action.alert(
                "Add sort order to {table}: ORDER BY (event_time, source_ip). "
                "This optimizes range scans within partitions and enables efficient "
                "IP-based forensics on time-bounded data.",
                severity="info",
            )
        ),
        priority=700,
        category="optimization",
        description="Suggest sort order for forensic query patterns",
    ),

    Rule(
        "ct_consider_z_order",
        conditions=when(
            ("cloudtrail_table.*.events_per_day", ">", 5_000_000),
            ("schema_state.*.is_z_ordered", "==", False),
            ("cloudtrail_table.*.unique_ips", ">", 1000),
            ("cloudtrail_table.*.unique_users", ">", 100),
        ),
        actions=then(
            Action.alert(
                "Consider Z-ordering {table} on (source_ip, user_arn) within time partitions. "
                "At this scale with high cardinality users/IPs, Z-ordering provides "
                "significant speedup for multi-column forensic queries.",
                severity="info",
            )
        ),
        priority=650,
        category="optimization",
        description="Suggest Z-ordering for high-cardinality forensic queries",
    ),

    # --- Data Lifecycle Rules ---
    Rule(
        "ct_implement_data_tiering",
        conditions=when(
            ("cloudtrail_table.*.days_of_data", ">", 30),
            ("cloudtrail_table.*.total_size_gb", ">", 100),
        ),
        actions=then(
            Action.alert(
                "Implement data tiering for {table}. With {days_of_data} days of data:\n"
                "  - Hot (0-7d): Keep on fast storage, optimize for query speed\n"
                "  - Warm (7-30d): Balance compression and access\n"
                "  - Cold (30+d): Maximum compression, consider archive storage\n"
                "Use table properties: write.metadata.metrics.default=truncate(16) for cold data",
                severity="info",
            )
        ),
        priority=600,
        category="lifecycle",
        description="Suggest data tiering for large historical datasets",
    ),

    Rule(
        "ct_retention_policy",
        conditions=when(
            ("cloudtrail_table.*.days_of_data", ">", 90),
            ("cloudtrail_table.*.snapshot_count", ">", 100),
        ),
        actions=then(
            Action.alert(
                "Review retention policy for {table}. Consider:\n"
                "  - Expire snapshots older than 7 days (retain 10 for time-travel)\n"
                "  - Archive data older than 90 days to separate cold table\n"
                "  - Delete data older than compliance requirement (typically 1-7 years)",
                severity="info",
            )
        ),
        priority=550,
        category="lifecycle",
        description="Suggest retention policy review for long-lived tables",
    ),

    # --- Query Pattern Optimization Rules ---
    Rule(
        "ct_optimize_for_investigations",
        conditions=when(
            ("cloudtrail_table.*.pct_queries_time_filtered", ">", 80),
            ("cloudtrail_table.*.has_time_partition", "==", True),
            ("schema_state.*.has_user_column", "==", False),
        ),
        actions=then(
            Action.alert(
                "Optimize {table} for security investigations:\n"
                "1. Add user_arn column for fast user-centric queries\n"
                "2. Add source_ip column for IP hunting\n"
                "3. Sort by (event_time, user_arn) within partitions\n"
                "This covers 90%+ of incident response query patterns.",
                severity="info",
            )
        ),
        priority=500,
        category="optimization",
        description="Holistic optimization for security investigation patterns",
    ),
]


# === CloudTrail Schema Goals for What-If Analysis ===

CLOUDTRAIL_GOALS = [
    Goal(
        "production_ready",
        conditions=[
            ("cloudtrail_table.*.has_time_partition", "==", True),
            ("schema_state.*.has_user_column", "==", True),
            ("schema_state.*.has_ip_column", "==", True),
            ("cloudtrail_table.*.avg_file_size_mb", ">=", 32),
            ("cloudtrail_table.*.snapshot_count", "<=", 100),
        ],
        description="Schema is optimized for production security workloads",
        priority=100,
    ),

    Goal(
        "time_queries_optimized",
        conditions=[
            ("cloudtrail_table.*.has_time_partition", "==", True),
        ],
        description="Time-bounded queries can leverage partition pruning",
        priority=90,
    ),

    Goal(
        "user_investigations_optimized",
        conditions=[
            ("cloudtrail_table.*.has_time_partition", "==", True),
            ("schema_state.*.has_user_column", "==", True),
            ("schema_state.*.is_sorted", "==", True),
        ],
        description="User-centric security investigations are optimized",
        priority=85,
    ),

    Goal(
        "ip_forensics_optimized",
        conditions=[
            ("cloudtrail_table.*.has_time_partition", "==", True),
            ("schema_state.*.has_ip_column", "==", True),
        ],
        description="IP-based threat hunting is optimized",
        priority=80,
    ),

    Goal(
        "error_detection_optimized",
        conditions=[
            ("schema_state.*.has_error_column", "==", True),
        ],
        description="Error/anomaly detection queries are optimized",
        priority=75,
    ),

    Goal(
        "json_parsing_eliminated",
        conditions=[
            ("schema_state.*.has_user_column", "==", True),
            ("schema_state.*.has_ip_column", "==", True),
            ("schema_state.*.has_error_column", "==", True),
            ("schema_state.*.has_event_source_column", "==", True),
            ("schema_state.*.has_region_column", "==", True),
        ],
        description="Hot fields denormalized, no JSON parsing needed for common queries",
        priority=70,
    ),

    Goal(
        "storage_optimized",
        conditions=[
            ("cloudtrail_table.*.avg_file_size_mb", ">=", 32),
            ("cloudtrail_table.*.snapshot_count", "<=", 50),
            ("cloudtrail_table.*.file_count", "<=", 1000),
        ],
        description="Storage is compacted and snapshots are managed",
        priority=60,
    ),
]


class CloudTrailOptimizer:
    """
    CloudTrail-specific schema optimizer with what-if analysis.

    Example:
        optimizer = CloudTrailOptimizer()

        # Analyze current state
        stats = CloudTrailTableStats(
            table_name="cloudtrail_events",
            events_per_day=5_000_000,
            has_time_partition=False,
            has_json_blob=True,
            unique_users=500,
            unique_ips=10000,
        )
        optimizer.analyze(stats)

        # Get recommendations
        for rec in optimizer.get_recommendations():
            print(f"{rec['priority']}: {rec['description']}")

        # What-if: "If I add time partitioning, what goals become achievable?"
        result = optimizer.what_if("production_ready", [
            ("cloudtrail_table.cloudtrail_events.has_time_partition", True),
        ])
        print(result.summary)

        # What's blocking production readiness?
        gaps = optimizer.analyze_gaps("production_ready")
        print(f"Missing: {gaps.missing_facts}")
    """

    def __init__(self):
        self.engine = ReteEngine()
        self.backward_engine = BackwardChainer()

        # Add rules
        for rule in CLOUDTRAIL_RULES:
            self.engine.add_rule(rule)

        # Add goals
        for goal in CLOUDTRAIL_GOALS:
            self.backward_engine.add_goal(goal)

    def analyze(self, stats: CloudTrailTableStats) -> None:
        """Load table statistics into the optimizer."""
        for fact in stats.to_facts():
            self.engine.assert_fact(fact)
            self.backward_engine.assert_fact(fact)

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Get prioritized optimization recommendations."""
        result = self.engine.solve()

        recommendations = []
        for activation in result.activations:
            for action in activation.actions:
                recommendations.append({
                    "rule_id": activation.rule_id,
                    "priority": activation.priority,
                    "category": activation.rule.category,
                    "description": activation.rule.description,
                    "action": action.action_type,
                    "message": action.params.get("message", "") if action.params else "",
                    "severity": action.params.get("severity", "info") if action.params else "info",
                })

        return sorted(recommendations, key=lambda r: -r["priority"])

    def what_if(self, goal_id: str, hypothetical_changes: list[tuple[str, Any]]) -> "Explanation":
        """
        What-if analysis: What goals become achievable if we make these changes?

        Args:
            goal_id: Goal to evaluate (e.g., "production_ready")
            hypothetical_changes: List of (fact_pattern, value) tuples
                e.g., [("cloudtrail_table.events.has_time_partition", True)]

        Returns:
            Explanation showing whether goal would be proven
        """
        from .backward import Explanation

        # Create hypothetical facts
        hypothetical_facts = []
        for pattern, value in hypothetical_changes:
            parts = pattern.split(".")
            if len(parts) >= 3:
                fact_type, fact_id, attr = parts[0], parts[1], ".".join(parts[2:])
                fact = Fact(fact_type, fact_id, **{attr: value})
                hypothetical_facts.append(fact)

        return self.backward_engine.what_if(goal_id, hypothetical_facts)

    def analyze_gaps(self, goal_id: str):
        """Analyze what's missing to achieve a goal."""
        return self.backward_engine.analyze_gaps(goal_id)

    def explain_goal(self, goal_id: str):
        """Get explanation of current goal status."""
        return self.backward_engine.explain(goal_id)

    def list_goals(self) -> list[dict[str, Any]]:
        """List all optimization goals with current status."""
        goals = []
        for goal_id, goal in self.backward_engine.goals.items():
            status = self.backward_engine.evaluate_goal(goal_id)
            goals.append({
                "goal_id": goal_id,
                "description": goal.description,
                "status": status.value,
                "priority": goal.priority,
            })
        return sorted(goals, key=lambda g: -g["priority"])

    def get_optimization_path(self, target_goal: str = "production_ready") -> list[dict]:
        """
        Get a step-by-step optimization path to reach a goal.

        Uses backward chaining to find what needs to change.
        For DISPROVEN goals, looks at blocking conditions to determine required actions.
        """
        gaps = self.analyze_gaps(target_goal)

        if gaps.status == ProofStatus.PROVEN:
            return [{"status": "achieved", "message": f"Goal '{target_goal}' already achieved"}]

        steps = []

        # Build steps from blocking conditions (things that need to change)
        for i, bc in enumerate(gaps.blocking_conditions, 1):
            pattern = bc["pattern"]
            action = self._pattern_to_action(pattern)

            # Estimate cost based on action type
            cost_map = {
                "ADD_PARTITION": 3,  # Schema change, requires rewrite
                "ADD_COLUMN": 2,     # Schema evolution
                "ADD_SORT_ORDER": 2,
                "COMPACT": 2,        # Maintenance operation
                "EXPIRE_SNAPSHOTS": 1,  # Quick operation
            }
            cost = cost_map.get(action["action"], 1)

            steps.append({
                "step": i,
                "fact_needed": pattern,
                "current_value": bc.get("actual", "unknown"),
                "required_value": bc.get("expected"),
                "action": action["action"],
                "description": action["description"],
                "cost": cost,
            })

        # Sort by cost (do cheap things first)
        steps.sort(key=lambda s: s["cost"])
        # Renumber
        for i, step in enumerate(steps, 1):
            step["step"] = i

        return steps

    def _pattern_to_action(self, pattern: str) -> dict:
        """Map a fact pattern to a concrete action."""
        actions = {
            "has_time_partition": {
                "action": "ADD_PARTITION",
                "description": "Add time-based partitioning: ALTER TABLE ... ADD PARTITION FIELD hours(event_time)",
            },
            "has_user_column": {
                "action": "ADD_COLUMN",
                "description": "Add user_arn column extracted from userIdentity.arn",
            },
            "has_ip_column": {
                "action": "ADD_COLUMN",
                "description": "Add source_ip column for threat hunting",
            },
            "has_error_column": {
                "action": "ADD_COLUMN",
                "description": "Add error_code column for anomaly detection",
            },
            "is_sorted": {
                "action": "ADD_SORT_ORDER",
                "description": "Add sort order: REPLACE SORT ORDER FOR ... WITH (event_time, source_ip)",
            },
            "avg_file_size_mb": {
                "action": "COMPACT",
                "description": "Run compaction to achieve target file size",
            },
            "snapshot_count": {
                "action": "EXPIRE_SNAPSHOTS",
                "description": "Expire old snapshots to reduce metadata overhead",
            },
        }

        for key, action in actions.items():
            if key in pattern:
                return action

        return {"action": "UNKNOWN", "description": f"Resolve: {pattern}"}


def create_cloudtrail_optimizer() -> CloudTrailOptimizer:
    """Factory function to create a configured CloudTrail optimizer."""
    return CloudTrailOptimizer()
