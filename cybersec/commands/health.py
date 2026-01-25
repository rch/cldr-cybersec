"""Health commands for unified command system.

Commands:
    /health                 - Run FMEA health diagnostics
    /health pyflink         - PyFlink diagnostics
    /health diagnose <id>   - Diagnose specific failure mode
    /health fix <id>        - Remediation for failure mode
"""

from io import StringIO
from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_health(cmd: ParsedCommand) -> CommandResult:
    """Run FMEA-based health diagnostics.

    Options:
        --category, -c <cat>  Specific category (iceberg, flink, infra, data)
        --quick, -q           Run only critical checks
        --json, -j            Output as JSON
    """
    from ..bootstrap import BootstrapService
    from ..health.models import HealthContext
    from ..health.runner import run_health_check

    service = BootstrapService()
    config = service.get_config()

    ctx = HealthContext(
        config=config,
        flink_url=config.flink_url or "http://localhost:8081",
        minio_endpoint=config.minio_endpoint or "http://localhost:9010",
        polaris_url=config.polaris_api_url or "http://localhost:8181",
        postgres_port=config.postgres_port or 5438,
        browser_port=config.iceberg_browser_port or 5050,
    )

    category = cmd.options.get("category")
    quick = cmd.options.get("quick", False)

    report = await run_health_check(ctx, category=category, quick=quick)
    data = report.to_dict()

    # Format for human display
    formatted = _format_health_report(data)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


async def cmd_health_pyflink(cmd: ParsedCommand) -> CommandResult:
    """Gather diagnostic information for PyFlink job failures.

    Use when PyFlink jobs fail with "Python process exits with code: 1".

    Options:
        --json, -j  Output as JSON
    """
    from ..health.pyflink_diagnostics import gather_pyflink_diagnostics

    data = await gather_pyflink_diagnostics()
    formatted = _format_pyflink_diagnostics(data)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


async def cmd_health_diagnose(cmd: ParsedCommand) -> CommandResult:
    """Get detailed diagnosis for a specific failure mode.

    Args:
        <id>  Failure mode ID (e.g., FLINK_001, ICE_002)

    Options:
        --json, -j  Output as JSON
    """
    from ..bootstrap import BootstrapService
    from ..health.models import HealthContext
    from ..health.runner import get_runner

    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: failure_mode_id (e.g., FLINK_001)",
        )

    failure_mode_id = cmd.args[0].upper()

    service = BootstrapService()
    config = service.get_config()

    ctx = HealthContext(
        config=config,
        flink_url=config.flink_url or "http://localhost:8081",
        minio_endpoint=config.minio_endpoint or "http://localhost:9010",
        polaris_url=config.polaris_api_url or "http://localhost:8181",
        postgres_port=config.postgres_port or 5438,
        browser_port=config.iceberg_browser_port or 5050,
    )

    runner = get_runner()
    data = await runner.diagnose(failure_mode_id, ctx)

    if data.get("error"):
        return CommandResult(
            success=False,
            error=data["error"],
        )

    formatted = _format_diagnosis(data, failure_mode_id)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


async def cmd_health_fix(cmd: ParsedCommand) -> CommandResult:
    """Attempt remediation for a failure mode.

    For TIER_0/TIER_1 issues (RPN <= 200), can auto-remediate.
    For TIER_2/MANUAL issues (RPN > 200), returns instructions only.

    Args:
        <id>  Failure mode ID to fix (e.g., ICE_001)

    Options:
        --execute   Execute remediation (default: dry-run)
        --json, -j  Output as JSON
    """
    from ..health.catalog import get_failure_mode

    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: failure_mode_id (e.g., FLINK_001)",
        )

    failure_mode_id = cmd.args[0].upper()
    dry_run = not cmd.options.get("execute", False)

    failure_mode = get_failure_mode(failure_mode_id)
    if not failure_mode:
        return CommandResult(
            success=False,
            error=f"Unknown failure mode: {failure_mode_id}",
        )

    rpn = failure_mode.calculate_rpn()
    tier = rpn.tier

    data = {
        "failure_mode_id": failure_mode_id,
        "tier": tier.name,
        "tier_value": int(tier),
        "requires_approval": rpn.requires_approval,
        "dry_run": dry_run,
        "action": "manual_required" if rpn.requires_approval else "dry_run" if dry_run else "would_execute",
        "instructions": failure_mode.remediation_steps,
        "symptom": failure_mode.symptom,
        "target_state": f"Resolve {failure_mode.name}",
    }

    formatted = _format_fix(data, failure_mode_id)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


# === Formatting helpers ===

def _format_health_report(data: dict) -> str:
    """Format health report for human display."""
    lines = []
    status = data.get("status", "unknown")
    status_marker = {"healthy": "✓", "degraded": "⚠", "critical": "✗"}.get(status, "?")

    lines.append(f"Health Status: {status_marker} {status.upper()}")
    lines.append("")

    checks = data.get("checks", {})
    for cat_name, cat_checks in checks.items():
        lines.append(f"{cat_name.upper()}:")
        for check in cat_checks:
            check_status = check.get("status", "unknown")
            icon = {"ok": "✓", "warning": "⚠", "critical": "✗", "error": "!", "skipped": "-"}.get(check_status, "?")
            lines.append(f"  {icon} {check.get('message', check.get('name', 'unknown'))}")
            if check.get("remediation"):
                lines.append(f"      → {check['remediation']}")
        lines.append("")

    issues = data.get("issues", [])
    if issues:
        lines.append("Issues Detected:")
        for issue in issues:
            lines.append(f"  • [{issue.get('failure_mode_id', '?')}] {issue.get('message', 'unknown')}")
            lines.append(f"    RPN: {issue.get('rpn', 'N/A')} | {issue.get('remediation', 'No remediation')}")
        lines.append("")

    recommendations = data.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations:")
        for rec in recommendations:
            lines.append(f"  • {rec}")

    return "\n".join(lines)


def _format_pyflink_diagnostics(data: dict) -> str:
    """Format PyFlink diagnostics for human display."""
    lines = []
    lines.append("PyFlink Diagnostics")
    lines.append("=" * 40)

    # Platform
    platform = data.get("platform", {})
    lines.append("")
    lines.append("Platform:")
    lines.append(f"  System: {platform.get('system', 'unknown')}")
    lines.append(f"  Release: {platform.get('release', 'unknown')}")
    lines.append(f"  Machine: {platform.get('machine', 'unknown')}")
    if platform.get("is_macos"):
        lines.append("  ⚠ macOS detected - check Python path configuration")

    # Python environment
    py_env = data.get("python_environment", {})
    lines.append("")
    lines.append("Python Environment:")
    lines.append(f"  Version: {py_env.get('version', 'unknown').split()[0]}")
    lines.append(f"  Executable: {py_env.get('executable', 'unknown')}")
    lines.append(f"  System python3: {py_env.get('system_python3', 'unknown')}")
    lines.append(f"  PYTHONPATH: {py_env.get('pythonpath', 'not set')}")

    if py_env.get("pyflink_version"):
        lines.append(f"  ✓ PyFlink: {py_env.get('pyflink_version')}")
        lines.append(f"    Location: {py_env.get('pyflink_location', 'unknown')}")
    elif py_env.get("pyflink_error"):
        lines.append(f"  ✗ PyFlink: NOT INSTALLED")
        lines.append(f"    Error: {py_env.get('pyflink_error')}")

    kafka_status = py_env.get("kafka_python", "unknown")
    if kafka_status == "installed":
        lines.append(f"  ✓ kafka-python: installed")
    else:
        lines.append(f"  ✗ kafka-python: {kafka_status}")

    if py_env.get("devenv_python"):
        lines.append(f"  Devenv Python: {py_env.get('devenv_python')}")
        exists = py_env.get("devenv_python_exists", False)
        lines.append(f"    Exists: {'yes' if exists else 'no'}")

    # Flink config
    flink_cfg = data.get("flink_config", {})
    lines.append("")
    lines.append("Flink Configuration:")
    lines.append(f"  FLINK_HOME: {flink_cfg.get('flink_home', 'not set')}")
    lines.append(f"  Exists: {'yes' if flink_cfg.get('flink_home_exists') else 'no'}")
    lines.append(f"  Binary exists: {'yes' if flink_cfg.get('flink_binary_exists') else 'no'}")

    python_settings = flink_cfg.get("python_settings", [])
    if python_settings and python_settings != ["none configured"]:
        lines.append("  Python settings in flink-conf.yaml:")
        for setting in python_settings:
            lines.append(f"    {setting}")
    else:
        lines.append("  No Python settings in flink-conf.yaml")

    # Logs
    logs = data.get("logs", {})
    lines.append("")
    lines.append("Logs:")

    submit_log = logs.get("submit_log")
    if isinstance(submit_log, dict):
        lines.append(f"  Submit log: {submit_log.get('path', 'unknown')}")
        lines.append(f"    Total lines: {submit_log.get('total_lines', 0)}")
        errors = submit_log.get("recent_errors", [])
        if errors and errors != ["no errors found in recent lines"]:
            lines.append("    Recent errors:")
            for err in errors[-5:]:
                lines.append(f"      {err[:80]}...")
        else:
            lines.append("    ✓ No recent errors")
    else:
        lines.append(f"  Submit log: {submit_log}")

    tm_log = logs.get("taskmanager")
    if isinstance(tm_log, dict):
        lines.append(f"  TaskManager log: {tm_log.get('path', 'unknown')}")
        py_lines = tm_log.get("python_related_lines", [])
        if py_lines and py_lines != ["no python-related entries"]:
            lines.append("    Python-related entries:")
            for line in py_lines[-3:]:
                lines.append(f"      {line[:80]}...")

    jm_log = logs.get("jobmanager")
    if isinstance(jm_log, dict):
        lines.append(f"  JobManager log: {jm_log.get('path', 'unknown')}")
        py_lines = jm_log.get("python_related_lines", [])
        if py_lines and py_lines != ["no python-related entries"]:
            lines.append("    Python-related entries:")
            for line in py_lines[-3:]:
                lines.append(f"      {line[:80]}...")

    # Recommendations
    recommendations = data.get("recommendations", [])
    if recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for rec in recommendations:
            lines.append(f"  • {rec}")

    return "\n".join(lines)


def _format_diagnosis(data: dict, failure_mode_id: str) -> str:
    """Format diagnosis for human display."""
    lines = []
    lines.append(f"Diagnosis: {failure_mode_id}")
    lines.append("=" * 40)

    fm = data.get("failure_mode", {})
    lines.append("")
    lines.append(f"Failure Mode: {fm.get('name', 'unknown')}")
    lines.append(f"Symptom: {fm.get('symptom', 'unknown')}")
    lines.append(f"Category: {fm.get('category', 'unknown')}")

    rpn = data.get("rpn", {})
    lines.append("")
    lines.append("Risk Priority Number:")
    lines.append(f"  Severity: {rpn.get('severity', 'N/A')}")
    lines.append(f"  Occurrence: {rpn.get('occurrence', 'N/A')}")
    lines.append(f"  Detection: {rpn.get('detection', 'N/A')}")
    lines.append(f"  RPN Score: {rpn.get('rpn', 'N/A')}")
    lines.append(f"  Tier: {rpn.get('tier', 'N/A')}")

    check_result = data.get("check_result", {})
    status = check_result.get("status", "unknown")
    lines.append("")
    lines.append(f"Current Status: {status.upper()}")
    lines.append(f"  {check_result.get('message', 'No message')}")

    remediation = data.get("remediation_steps", [])
    if remediation:
        lines.append("")
        lines.append("Remediation Steps:")
        for i, step in enumerate(remediation, 1):
            lines.append(f"  {i}. {step}")

    return "\n".join(lines)


def _format_fix(data: dict, failure_mode_id: str) -> str:
    """Format fix result for human display."""
    lines = []
    lines.append(f"Fix: {failure_mode_id}")
    lines.append("=" * 40)

    lines.append("")
    lines.append(f"Escalation Tier: {data.get('tier', 'N/A')} (RPN: {data.get('tier_value', 'N/A')})")

    if data.get("requires_approval"):
        lines.append("⚠ This issue requires manual intervention.")
    elif data.get("dry_run"):
        lines.append("ℹ DRY RUN - showing what would be done")
    else:
        lines.append("✓ Executing remediation...")

    instructions = data.get("instructions", [])
    if instructions:
        lines.append("")
        lines.append("Remediation Instructions:")
        for i, step in enumerate(instructions, 1):
            lines.append(f"  {i}. {step}")

    return "\n".join(lines)


# === Register commands ===

def register_health_commands():
    """Register all health commands."""
    register_command(
        "health",
        cmd_health,
        description="Run FMEA-based health diagnostics",
        options=[
            {"name": "category", "short": "c", "description": "Category to check (iceberg, flink, infra, data)"},
            {"name": "quick", "short": "q", "description": "Run only critical checks"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/health",
            "/health --category flink",
            "/health --quick --json",
        ],
    )

    register_command(
        "health.pyflink",
        cmd_health_pyflink,
        description="Gather PyFlink diagnostic information",
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/health pyflink",
            "/health pyflink --json",
        ],
    )

    register_command(
        "health.diagnose",
        cmd_health_diagnose,
        description="Diagnose a specific failure mode",
        args=[
            {"name": "failure_mode_id", "required": True, "description": "Failure mode ID (e.g., FLINK_001)"},
        ],
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/health diagnose FLINK_001",
            "/health diagnose ICE_002 --json",
        ],
    )

    register_command(
        "health.fix",
        cmd_health_fix,
        description="Remediation for a failure mode",
        args=[
            {"name": "failure_mode_id", "required": True, "description": "Failure mode ID to fix"},
        ],
        options=[
            {"name": "execute", "description": "Execute remediation (default: dry-run)"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/health fix ICE_001",
            "/health fix FLINK_002 --execute",
        ],
    )
