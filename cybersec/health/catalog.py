"""FMEA Failure Mode Catalog for cybersec toolkit.

Defines failure modes with base FMEA scores for:
- Iceberg/PyIceberg issues (ICE_*)
- Flink issues (FLINK_*)
- Infrastructure issues (INFRA_*)
- Data quality issues (DATA_*)
"""

from .models import AutomationLevel, FailureMode

# Iceberg/PyIceberg failure modes
ICE_001 = FailureMode(
    failure_mode_id="ICE_001",
    category="iceberg",
    name="Scan Memory Exhaustion",
    description="PyIceberg scan loads too much data into memory, causing OOM",
    base_severity=8,   # High impact - browser crashes
    base_occurrence=4,  # Moderate - happens with large tables
    base_detection=3,   # Good - can monitor memory
    symptom="Browser OOM, 7GB+ memory usage, slow/timeout responses on /api/events",
    cause="PyIceberg scan().to_pandas() without limit on large table (420k+ records)",
    detection_method="Monitor process memory via psutil, warn at 2GB, critical at 4GB",
    remediation_steps=[
        "Restart iceberg_browser process",
        "Verify all scans use limit=N parameter",
        "Check MAX_SCAN_ROWS in iceberg_browser.py (line 307)",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

ICE_002 = FailureMode(
    failure_mode_id="ICE_002",
    category="iceberg",
    name="Catalog Connection Failed",
    description="Cannot connect to Polaris REST catalog",
    base_severity=9,   # Critical - no table access
    base_occurrence=3,  # Low-moderate - usually stable
    base_detection=2,   # Easy to detect
    symptom="500 errors on /api/tables, /api/events returns 'Table not found'",
    cause="Polaris REST catalog unreachable or credentials invalid",
    detection_method="catalog.list_namespaces() call succeeds",
    remediation_steps=[
        "Check Polaris is running: curl http://localhost:8181/q/health/ready",
        "Verify credentials in POLARIS_CLIENT_ID/SECRET",
        "Restart Polaris: devenv tasks run restart:polaris",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

ICE_003 = FailureMode(
    failure_mode_id="ICE_003",
    category="iceberg",
    name="Stale Data",
    description="No new snapshots being written to table",
    base_severity=5,   # Moderate - data is old but system works
    base_occurrence=4,  # Moderate - pipeline can stall
    base_detection=4,   # Moderate - need to check timestamps
    symptom="Dashboard shows old events, real-time metrics stuck",
    cause="Pipeline not writing new snapshots (Flink job stopped, writer failed)",
    detection_method="Compare latest snapshot timestamp vs current time (> 30 min = stale)",
    remediation_steps=[
        "Check Flink job status: curl http://localhost:8081/jobs/overview",
        "Verify iceberg_writer is running",
        "Check for write errors in Flink logs",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

# Flink failure modes
FLINK_001 = FailureMode(
    failure_mode_id="FLINK_001",
    category="flink",
    name="TaskManager Missing",
    description="No Flink TaskManagers registered with JobManager",
    base_severity=9,   # Critical - jobs can't run
    base_occurrence=3,  # Low-moderate - usually stable
    base_detection=2,   # Easy to detect
    symptom="Jobs stuck in CREATED state, 'No available slots' errors",
    cause="TaskManager process not started or crashed",
    detection_method="Query /taskmanagers API, check count > 0",
    remediation_steps=[
        "Check TaskManager process: pgrep -f TaskManager",
        "Start TaskManager: $FLINK_HOME/bin/taskmanager.sh start",
        "Check logs: $FLINK_HOME/log/flink-*-taskexecutor-*.log",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

FLINK_002 = FailureMode(
    failure_mode_id="FLINK_002",
    category="flink",
    name="Job Failed",
    description="Flink job crashed or failed",
    base_severity=8,   # High - no data processing
    base_occurrence=4,  # Moderate - can happen
    base_detection=2,   # Easy to detect
    symptom="No new data arriving in Iceberg table",
    cause="Job exception, resource exhaustion, or dependency failure",
    detection_method="Query /jobs/overview API for jobs in FAILED state",
    remediation_steps=[
        "Check job status: curl http://localhost:8081/jobs/overview",
        "View exceptions: curl http://localhost:8081/jobs/<jid>/exceptions",
        "Restart job: flink run -py flink_jobs/cloudtrail_datagen.py",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

FLINK_003 = FailureMode(
    failure_mode_id="FLINK_003",
    category="flink",
    name="Checkpoint Stale",
    description="Flink checkpoints not being created",
    base_severity=6,   # Moderate - data loss risk on restart
    base_occurrence=3,  # Low-moderate
    base_detection=5,   # Moderate - need to check files
    symptom="Data loss risk on restart, checkpoint directory has old files",
    cause="Checkpoint failures due to storage issues or job problems",
    detection_method="Check checkpoint directory for recent files (< 5 min old)",
    remediation_steps=[
        "Check checkpoint config in Flink job",
        "Verify MinIO storage is healthy",
        "Review Flink logs for checkpoint errors",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.C,
)

# Infrastructure failure modes
INFRA_001 = FailureMode(
    failure_mode_id="INFRA_001",
    category="infra",
    name="PostgreSQL Down",
    description="PostgreSQL database not running",
    base_severity=9,   # Critical - catalog needs DB
    base_occurrence=2,  # Low - usually stable
    base_detection=2,   # Easy to detect
    symptom="Catalog operations fail, 'connection refused' errors",
    cause="PostgreSQL process not running or port blocked",
    detection_method="TCP connection test to port 5438",
    remediation_steps=[
        "Check PostgreSQL: pg_isready -p 5438",
        "Start with devenv: devenv up postgres",
        "Check logs: journalctl -u postgresql",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

INFRA_002 = FailureMode(
    failure_mode_id="INFRA_002",
    category="infra",
    name="MinIO Unhealthy",
    description="MinIO object storage not responding",
    base_severity=9,   # Critical - no data storage
    base_occurrence=2,  # Low - usually stable
    base_detection=2,   # Easy to detect
    symptom="Write failures, 'connection refused' on S3 operations",
    cause="MinIO process not running or storage full",
    detection_method="Check /minio/health/live endpoint",
    remediation_steps=[
        "Check MinIO health: curl http://localhost:9010/minio/health/live",
        "Start with devenv: devenv up minio",
        "Check disk space: df -h /path/to/minio/data",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

INFRA_003 = FailureMode(
    failure_mode_id="INFRA_003",
    category="infra",
    name="Polaris Degraded",
    description="Polaris catalog service degraded or slow",
    base_severity=7,   # High - catalog operations affected
    base_occurrence=3,  # Low-moderate
    base_detection=3,   # Good detection
    symptom="Slow catalog operations, intermittent 503 errors",
    cause="Polaris overloaded or resource constrained",
    detection_method="Check /q/health/ready endpoint response time",
    remediation_steps=[
        "Check Polaris health: curl http://localhost:8182/q/health/ready",
        "Restart Polaris: devenv tasks run restart:polaris",
        "Check memory usage of Polaris process",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

# Data quality failure modes
DATA_001 = FailureMode(
    failure_mode_id="DATA_001",
    category="data",
    name="Snapshot Accumulation",
    description="Too many snapshots accumulated in table",
    base_severity=4,   # Low-moderate - performance degradation
    base_occurrence=5,  # Moderate-high - happens over time
    base_detection=4,   # Moderate
    symptom="Slow metadata operations, increased memory usage",
    cause="Snapshot expiration not configured or not running",
    detection_method="Count snapshots in table.metadata.snapshots (> 100 = issue)",
    remediation_steps=[
        "Run snapshot expiration: table.expire_snapshots()",
        "Configure automatic expiration in writer",
        "Consider compaction for optimal read performance",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)


# Failure mode registry
FAILURE_MODES: dict[str, FailureMode] = {
    "ICE_001": ICE_001,
    "ICE_002": ICE_002,
    "ICE_003": ICE_003,
    "FLINK_001": FLINK_001,
    "FLINK_002": FLINK_002,
    "FLINK_003": FLINK_003,
    "INFRA_001": INFRA_001,
    "INFRA_002": INFRA_002,
    "INFRA_003": INFRA_003,
    "DATA_001": DATA_001,
}

# Category groupings
CATEGORIES: dict[str, list[str]] = {
    "iceberg": ["ICE_001", "ICE_002", "ICE_003"],
    "flink": ["FLINK_001", "FLINK_002", "FLINK_003"],
    "infra": ["INFRA_001", "INFRA_002", "INFRA_003"],
    "data": ["DATA_001"],
}

# Quick checks (critical infrastructure only)
QUICK_CHECKS: list[str] = [
    "INFRA_001",  # PostgreSQL
    "INFRA_002",  # MinIO
    "FLINK_001",  # TaskManager
    "ICE_002",    # Catalog connection
]


def get_failure_mode(failure_mode_id: str) -> FailureMode | None:
    """Get a failure mode by ID."""
    return FAILURE_MODES.get(failure_mode_id)


def get_category_modes(category: str) -> list[FailureMode]:
    """Get all failure modes in a category."""
    mode_ids = CATEGORIES.get(category, [])
    return [FAILURE_MODES[mid] for mid in mode_ids if mid in FAILURE_MODES]


def get_quick_check_modes() -> list[FailureMode]:
    """Get failure modes for quick health check."""
    return [FAILURE_MODES[mid] for mid in QUICK_CHECKS if mid in FAILURE_MODES]
