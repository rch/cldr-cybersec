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

# PyFlink-specific failure modes
PYFLINK_001 = FailureMode(
    failure_mode_id="PYFLINK_001",
    category="pyflink",
    name="PyFlink Not Installed",
    description="PyFlink package not installed or not importable",
    base_severity=9,   # Critical - PyFlink jobs can't run
    base_occurrence=4,  # Moderate - common on fresh setup
    base_detection=1,   # Very easy to detect
    symptom="ImportError when running PyFlink jobs, 'No module named pyflink'",
    cause="apache-flink package not installed in Python environment",
    detection_method="import pyflink succeeds",
    remediation_steps=[
        "Install PyFlink: uv pip install apache-flink",
        "Verify installation: python -c 'import pyflink; print(pyflink.__version__)'",
        "Ensure using correct Python environment (devenv venv)",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_002 = FailureMode(
    failure_mode_id="PYFLINK_002",
    category="pyflink",
    name="Python Path Mismatch",
    description="Flink using different Python than PyFlink installed in",
    base_severity=8,   # High - jobs fail with cryptic errors
    base_occurrence=6,  # High on macOS - common issue
    base_detection=4,   # Moderate - need to compare paths
    symptom="'Python process exits with code: 1', PyFlink import errors in TaskManager logs",
    cause="PYFLINK_CLIENT_EXECUTABLE not set or points to wrong Python",
    detection_method="Compare sys.executable with flink-conf.yaml python settings",
    remediation_steps=[
        "Run: cybersec --cmd '/health fix pyflink' (auto-fixes config)",
        "Or manually add to flink-conf.yaml: python.client.executable: /path/to/python",
        "Then restart: devenv tasks run restart:clean",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

PYFLINK_003 = FailureMode(
    failure_mode_id="PYFLINK_003",
    category="pyflink",
    name="kafka-python Missing",
    description="kafka-python package required but not installed",
    base_severity=7,   # High - Kafka connectors fail
    base_occurrence=4,  # Moderate - common oversight
    base_detection=1,   # Very easy to detect
    symptom="'No module named kafka' errors, Kafka source/sink fails",
    cause="kafka-python package not installed",
    detection_method="import kafka succeeds",
    remediation_steps=[
        "Install kafka-python: uv pip install kafka-python",
        "Verify: python -c 'import kafka; print(\"OK\")'",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_004 = FailureMode(
    failure_mode_id="PYFLINK_004",
    category="pyflink",
    name="FLINK_HOME Not Set",
    description="FLINK_HOME environment variable not configured",
    base_severity=8,   # High - can't submit jobs
    base_occurrence=4,  # Moderate - common on fresh setup
    base_detection=1,   # Very easy to detect
    symptom="'flink' command not found, job submission fails",
    cause="Flink not installed or FLINK_HOME not exported",
    detection_method="FLINK_HOME env var set and points to valid directory",
    remediation_steps=[
        "Run bootstrap: devenv tasks run restart:clean (builds Flink on first run)",
        "Or set manually: export FLINK_HOME=/path/to/flink-1.20.1",
        "Verify: $FLINK_HOME/bin/flink --version",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_005 = FailureMode(
    failure_mode_id="PYFLINK_005",
    category="pyflink",
    name="macOS Python Configuration",
    description="macOS-specific Python path issues with Flink",
    base_severity=7,   # High - jobs fail
    base_occurrence=7,  # Very high on macOS
    base_detection=3,   # Good - can detect platform
    symptom="PyFlink works locally but fails in Flink cluster on macOS",
    cause="macOS has multiple Python installations, Flink picks wrong one",
    detection_method="Platform is Darwin AND python settings not in flink-conf.yaml",
    remediation_steps=[
        "Run: cybersec --cmd '/health fix pyflink' (auto-fixes config)",
        "Or manually edit $FLINK_HOME/conf/flink-conf.yaml with devenv Python path",
        "Then restart: devenv tasks run restart:clean",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.B,
)

PYFLINK_006 = FailureMode(
    failure_mode_id="PYFLINK_006",
    category="pyflink",
    name="Job Submission Log Errors",
    description="Errors detected in PyFlink job submission log",
    base_severity=6,   # Moderate-high
    base_occurrence=5,  # Moderate
    base_detection=2,   # Easy - check log file
    symptom="Job submission fails, errors in /tmp/cloudtrail_submit.log",
    cause="Various - check log for specific error",
    detection_method="Check /tmp/cloudtrail_submit.log for ERROR/Exception lines",
    remediation_steps=[
        "Review full log: cat /tmp/cloudtrail_submit.log",
        "Check for Python errors (import, syntax)",
        "Check for Flink errors (cluster connectivity, resource allocation)",
        "Verify Flink cluster is running: curl http://localhost:8081/overview",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.C,
)

PYFLINK_007 = FailureMode(
    failure_mode_id="PYFLINK_007",
    category="pyflink",
    name="Config Written But Not Applied",
    description="flink-conf.yaml has Python settings but Flink not using them",
    base_severity=8,   # High - fix appears successful but doesn't work
    base_occurrence=6,  # High - common after fix without restart
    base_detection=3,   # Moderate - need to check both config and runtime
    symptom="'Python process exits with code: 1' persists after /health fix pyflink",
    cause="Flink cluster not restarted after config change, JVM still using old config",
    detection_method="Config has python.executable but TaskManager logs show wrong Python",
    remediation_steps=[
        "Stop Flink cluster: $FLINK_HOME/bin/stop-cluster.sh",
        "Verify config: grep python $FLINK_HOME/conf/flink-conf.yaml",
        "Start Flink cluster: $FLINK_HOME/bin/start-cluster.sh",
        "Or run: devenv tasks run restart:clean",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_008 = FailureMode(
    failure_mode_id="PYFLINK_008",
    category="pyflink",
    name="Flink Cluster Stale After Config Change",
    description="Flink JVM processes running with old configuration",
    base_severity=7,   # High - silent failure
    base_occurrence=5,  # Moderate - happens when restart skipped
    base_detection=4,   # Moderate - need to compare timestamps
    symptom="Config file newer than Flink process start time",
    cause="Flink cluster not restarted after flink-conf.yaml modification",
    detection_method="Compare flink-conf.yaml mtime vs TaskManager process start time",
    remediation_steps=[
        "Stop Flink: $FLINK_HOME/bin/stop-cluster.sh",
        "Start Flink: $FLINK_HOME/bin/start-cluster.sh",
        "Verify processes restarted: ps aux | grep -i taskmanager",
        "Or run full restart: devenv tasks run restart:clean",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_009 = FailureMode(
    failure_mode_id="PYFLINK_009",
    category="pyflink",
    name="Python Executable Not Found by Flink",
    description="Configured Python path in flink-conf.yaml does not exist or is not executable",
    base_severity=8,   # High - jobs fail immediately
    base_occurrence=3,  # Low-moderate - config error
    base_detection=2,   # Easy - check file exists
    symptom="'Python process exits with code: 1', Python path in config invalid",
    cause="Configured python.executable path does not exist or changed",
    detection_method="Check if python.executable path from flink-conf.yaml exists and is executable",
    remediation_steps=[
        "Check configured path: grep python.executable $FLINK_HOME/conf/flink-conf.yaml",
        "Verify path exists: ls -la /path/to/python3",
        "Re-run fix to update path: cybersec --cmd '/health fix pyflink'",
        "Restart cluster: devenv tasks run restart:clean",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
)

PYFLINK_010 = FailureMode(
    failure_mode_id="PYFLINK_010",
    category="pyflink",
    name="FLINK_HOME Not Exported",
    description="FLINK_HOME env var not set in current shell (devenv sets it internally)",
    base_severity=5,   # Moderate - inconvenience, not blocking
    base_occurrence=6,  # High - common outside devenv shell
    base_detection=1,   # Very easy to detect
    symptom="$FLINK_HOME not available in shell, manual flink commands fail",
    cause="Running outside devenv shell or FLINK_HOME not exported",
    detection_method="Check if FLINK_HOME environment variable is set",
    remediation_steps=[
        "Enter devenv shell: devenv shell",
        "Or export manually: export FLINK_HOME=$(cybersec --cmd '/bootstrap info --json' | jq -r '.data.flink_home')",
        "Or use full path from bootstrap config",
    ],
    observation_level=AutomationLevel.A,
    solution_level=AutomationLevel.A,
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
    "PYFLINK_001": PYFLINK_001,
    "PYFLINK_002": PYFLINK_002,
    "PYFLINK_003": PYFLINK_003,
    "PYFLINK_004": PYFLINK_004,
    "PYFLINK_005": PYFLINK_005,
    "PYFLINK_006": PYFLINK_006,
    "PYFLINK_007": PYFLINK_007,
    "PYFLINK_008": PYFLINK_008,
    "PYFLINK_009": PYFLINK_009,
    "PYFLINK_010": PYFLINK_010,
    "INFRA_001": INFRA_001,
    "INFRA_002": INFRA_002,
    "INFRA_003": INFRA_003,
    "DATA_001": DATA_001,
}

# Category groupings
CATEGORIES: dict[str, list[str]] = {
    "iceberg": ["ICE_001", "ICE_002", "ICE_003"],
    "flink": ["FLINK_001", "FLINK_002", "FLINK_003"],
    "pyflink": ["PYFLINK_001", "PYFLINK_002", "PYFLINK_003", "PYFLINK_004", "PYFLINK_005", "PYFLINK_006", "PYFLINK_007", "PYFLINK_008", "PYFLINK_009", "PYFLINK_010"],
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
