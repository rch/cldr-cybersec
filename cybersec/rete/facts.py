"""
Fact definitions for RETE working memory.

Facts represent assertions about the current state of the system.
They are typed and have attributes that can be matched by rules.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FactType(Enum):
    """Categories of facts in working memory."""

    # Health check facts
    SERVICE = "service"  # Service health: postgres, flink, minio, polaris
    ISSUE = "issue"  # Detected issue with failure_mode_id
    CHECK = "check"  # Health check status
    CONFIG = "config"  # Configuration state

    # Iceberg facts
    TABLE = "table"  # Table metadata and statistics
    PARTITION = "partition"  # Partition information
    SNAPSHOT = "snapshot"  # Snapshot metadata
    FILE = "file"  # Data file statistics
    QUERY = "query"  # Query pattern information


@dataclass
class Fact:
    """
    A fact in working memory.

    Facts are identified by (fact_type, fact_id) and have typed attributes.
    Attributes can be:
    - bool: Represented as BoolVar
    - int: Represented as IntVar with bounds
    - float: Scaled to int (x1000) for CP-SAT compatibility
    - str: Stored but not directly constrainable (use for actions)

    Example:
        Fact("service", "postgres", healthy=True, port=5438, latency_ms=12)
        Fact("table", "cloudtrail", row_count=10_000_000, avg_file_size_mb=12)
    """

    fact_type: str | FactType
    fact_id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __init__(self, fact_type: str | FactType, fact_id: str, **attributes):
        self.fact_type = fact_type.value if isinstance(fact_type, FactType) else fact_type
        self.fact_id = fact_id
        self.attributes = attributes

    @property
    def key(self) -> str:
        """Unique key for this fact in working memory."""
        return f"{self.fact_type}.{self.fact_id}"

    def attr_key(self, attr: str) -> str:
        """Key for a specific attribute of this fact."""
        return f"{self.key}.{attr}"

    def __repr__(self) -> str:
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.attributes.items())
        return f"Fact({self.fact_type!r}, {self.fact_id!r}, {attrs})"


# --- Pre-defined fact templates for common use cases ---


def service_fact(
    service_id: str,
    *,
    healthy: bool,
    port: int | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> Fact:
    """Create a service health fact."""
    attrs = {"healthy": healthy}
    if port is not None:
        attrs["port"] = port
    if latency_ms is not None:
        attrs["latency_ms"] = latency_ms
    if error is not None:
        attrs["error"] = error
    return Fact(FactType.SERVICE, service_id, **attrs)


def issue_fact(
    failure_mode_id: str,
    *,
    rpn: int,
    severity: int,
    occurrence: int,
    detection: int,
    auto_fixable: bool = False,
    tier: int = 2,
    category: str = "",
) -> Fact:
    """Create an issue fact from FMEA failure mode."""
    return Fact(
        FactType.ISSUE,
        failure_mode_id,
        rpn=rpn,
        severity=severity,
        occurrence=occurrence,
        detection=detection,
        auto_fixable=auto_fixable,
        tier=tier,
        category=category,
    )


def check_fact(
    check_id: str,
    *,
    status: str,  # "ok", "warning", "critical", "error", "skipped"
    depends_on: list[str] | None = None,
    ran: bool = False,
) -> Fact:
    """Create a health check status fact."""
    return Fact(
        FactType.CHECK,
        check_id,
        status=status,
        depends_on=depends_on or [],
        ran=ran,
        # Numeric encoding for constraints
        is_ok=status == "ok",
        is_critical=status == "critical",
        is_skipped=status == "skipped",
    )


def table_fact(
    table_name: str,
    *,
    row_count: int,
    partition_count: int,
    file_count: int,
    total_size_mb: int,
    avg_file_size_mb: int,
    snapshot_count: int = 1,
    days_since_compaction: int = 0,
    has_sort_order: bool = False,
    partition_spec: str = "",  # e.g., "days(event_timestamp)"
) -> Fact:
    """Create an Iceberg table statistics fact."""
    return Fact(
        FactType.TABLE,
        table_name,
        row_count=row_count,
        partition_count=partition_count,
        file_count=file_count,
        total_size_mb=total_size_mb,
        avg_file_size_mb=avg_file_size_mb,
        snapshot_count=snapshot_count,
        days_since_compaction=days_since_compaction,
        has_sort_order=has_sort_order,
        partition_spec=partition_spec,
        # Derived metrics for rules
        files_per_partition=file_count // max(partition_count, 1),
        needs_compaction=avg_file_size_mb < 32 or file_count > partition_count * 10,
    )


def partition_fact(
    table_name: str,
    partition_value: str,
    *,
    file_count: int,
    total_size_mb: int,
    row_count: int,
    min_timestamp: int | None = None,  # epoch seconds
    max_timestamp: int | None = None,
) -> Fact:
    """Create an Iceberg partition statistics fact."""
    partition_id = f"{table_name}/{partition_value}"
    return Fact(
        FactType.PARTITION,
        partition_id,
        table_name=table_name,
        partition_value=partition_value,
        file_count=file_count,
        total_size_mb=total_size_mb,
        row_count=row_count,
        min_timestamp=min_timestamp or 0,
        max_timestamp=max_timestamp or 0,
        avg_file_size_mb=total_size_mb // max(file_count, 1),
    )


def query_pattern_fact(
    table_name: str,
    *,
    filters_on_partition: bool,
    filters_on_timestamp: bool,
    selects_all_columns: bool,
    uses_time_range: bool,
    avg_rows_scanned: int,
    avg_files_scanned: int,
) -> Fact:
    """Create a query pattern analysis fact."""
    return Fact(
        FactType.QUERY,
        f"{table_name}_pattern",
        table_name=table_name,
        filters_on_partition=filters_on_partition,
        filters_on_timestamp=filters_on_timestamp,
        selects_all_columns=selects_all_columns,
        uses_time_range=uses_time_range,
        avg_rows_scanned=avg_rows_scanned,
        avg_files_scanned=avg_files_scanned,
        # Efficiency indicators
        has_partition_pruning=filters_on_partition or filters_on_timestamp,
        is_full_scan=not filters_on_partition and avg_files_scanned > 100,
    )
