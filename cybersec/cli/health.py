"""Health CLI commands using Typer.

Provides CLI interface with naming parity to MCP health tools:
- cybersec health          -> bootstrap_health (MCP)
- cybersec health diagnose -> bootstrap_diagnose (MCP)
- cybersec health fix      -> bootstrap_fix (MCP)
- cybersec health pyflink  -> bootstrap_debug_pyflink (MCP)
"""

import typer
import asyncio
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

app = typer.Typer(
    name="health",
    help="FMEA-based health diagnostics for the cybersec environment",
    invoke_without_command=True,
)

console = Console()


@app.callback(invoke_without_command=True)
def health_default(
    ctx: typer.Context,
    category: Optional[str] = typer.Option(
        None, "--category", "-c",
        help="Specific category to check (iceberg, flink, infra, data)"
    ),
    quick: bool = typer.Option(
        False, "--quick", "-q",
        help="Run only critical infrastructure checks (fast)"
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j",
        help="Output as JSON"
    ),
):
    """Run FMEA-based health diagnostics on the cybersec environment.

    Goes beyond basic connectivity to check:
    - iceberg: PyIceberg memory usage, catalog connectivity, data freshness
    - flink: Job status, TaskManager availability, checkpoint health
    - infra: PostgreSQL, MinIO, Polaris connectivity
    - data: Snapshot accumulation, data quality

    Examples:
        cybersec health
        cybersec health --category flink
        cybersec health --quick
        cybersec health --json
    """
    # Only run if no subcommand was invoked
    if ctx.invoked_subcommand is not None:
        return

    import json as json_module
    from ..bootstrap import BootstrapService
    from ..health.models import HealthContext
    from ..health.runner import run_health_check

    service = BootstrapService()
    config = service.get_config()

    # Create health context
    ctx_health = HealthContext(
        config=config,
        flink_url=config.flink_url or "http://localhost:8081",
        minio_endpoint=config.minio_endpoint or "http://localhost:9010",
        polaris_url=config.polaris_api_url or "http://localhost:8181",
        postgres_port=config.postgres_port or 5438,
        browser_port=config.iceberg_browser_port or 5050,
    )

    # Run health checks
    report = asyncio.run(run_health_check(ctx_health, category=category, quick=quick))
    result = report.to_dict()

    if json_output:
        print(json_module.dumps(result, indent=2))
        return

    # Pretty print
    status = result.get("status", "unknown")
    status_color = {
        "healthy": "green",
        "degraded": "yellow",
        "critical": "red",
    }.get(status, "white")

    console.print(Panel.fit(
        f"[bold blue]Health Diagnostics[/bold blue]\n"
        f"Status: [{status_color}]{status.upper()}[/{status_color}]",
        border_style="blue"
    ))

    # Display checks by category
    checks = result.get("checks", {})
    for cat_name, cat_checks in checks.items():
        console.print(f"\n[bold]{cat_name.upper()}[/bold]")
        for check in cat_checks:
            check_status = check.get("status", "unknown")
            status_icon = {
                "ok": "[green]✓[/green]",
                "warning": "[yellow]⚠[/yellow]",
                "critical": "[red]✗[/red]",
                "error": "[red]![/red]",
                "skipped": "[dim]-[/dim]",
            }.get(check_status, "[white]?[/white]")
            console.print(f"  {status_icon} {check.get('message', check.get('name', 'unknown'))}")
            if check.get("remediation"):
                console.print(f"      [dim]→ {check['remediation']}[/dim]")

    # Display issues
    issues = result.get("issues", [])
    if issues:
        console.print("\n[bold red]Issues Detected:[/bold red]")
        for issue in issues:
            console.print(f"  • [{issue.get('failure_mode_id', '?')}] {issue.get('message', 'unknown')}")
            console.print(f"    RPN: {issue.get('rpn', 'N/A')} | {issue.get('remediation', 'No remediation')}")

    # Display recommendations
    recommendations = result.get("recommendations", [])
    if recommendations:
        console.print("\n[bold yellow]Recommendations:[/bold yellow]")
        for rec in recommendations:
            console.print(f"  • {rec}")


@app.command()
def diagnose(
    failure_mode_id: str = typer.Argument(..., help="Failure mode ID (e.g., ICE_001, FLINK_002)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get detailed diagnosis for a specific failure mode.

    Returns the heuristic definition, current check status,
    RPN calculation, and remediation steps.

    Examples:
        cybersec health diagnose FLINK_001
        cybersec health diagnose ICE_002 --json
    """
    import json as json_module
    from ..bootstrap import BootstrapService
    from ..health.models import HealthContext
    from ..health.runner import get_runner

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
    result = asyncio.run(runner.diagnose(failure_mode_id, ctx))

    if json_output:
        print(json_module.dumps(result, indent=2))
        return

    if result.get("error"):
        console.print(f"[red]Error:[/red] {result['error']}")
        raise typer.Exit(code=1)

    # Pretty print diagnosis
    fm = result.get("failure_mode", {})
    console.print(Panel.fit(
        f"[bold blue]Diagnosis: {failure_mode_id}[/bold blue]",
        border_style="blue"
    ))

    console.print(f"\n[bold]Failure Mode:[/bold] {fm.get('name', 'unknown')}")
    console.print(f"[bold]Symptom:[/bold] {fm.get('symptom', 'unknown')}")
    console.print(f"[bold]Category:[/bold] {fm.get('category', 'unknown')}")

    rpn = result.get("rpn", {})
    console.print(f"\n[bold]Risk Priority Number:[/bold]")
    console.print(f"  Severity: {rpn.get('severity', 'N/A')}")
    console.print(f"  Occurrence: {rpn.get('occurrence', 'N/A')}")
    console.print(f"  Detection: {rpn.get('detection', 'N/A')}")
    console.print(f"  RPN Score: {rpn.get('rpn', 'N/A')}")
    console.print(f"  Tier: {rpn.get('tier', 'N/A')}")

    check_result = result.get("check_result", {})
    status = check_result.get("status", "unknown")
    status_color = {
        "ok": "green",
        "warning": "yellow",
        "critical": "red",
    }.get(status, "white")
    console.print(f"\n[bold]Current Status:[/bold] [{status_color}]{status.upper()}[/{status_color}]")
    console.print(f"  {check_result.get('message', 'No message')}")

    remediation = result.get("remediation_steps", [])
    if remediation:
        console.print(f"\n[bold]Remediation Steps:[/bold]")
        for i, step in enumerate(remediation, 1):
            console.print(f"  {i}. {step}")


@app.command()
def fix(
    failure_mode_id: str = typer.Argument(..., help="Failure mode ID to fix (e.g., ICE_001)"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Show what would be done (default: dry-run)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Attempt remediation for a failure mode based on escalation tier.

    For TIER_0/TIER_1 issues (RPN <= 200), can auto-remediate.
    For TIER_2/MANUAL issues (RPN > 200), returns instructions only.

    Examples:
        cybersec health fix ICE_001
        cybersec health fix FLINK_002 --execute
    """
    import json as json_module
    from ..health.catalog import get_failure_mode

    failure_mode = get_failure_mode(failure_mode_id)
    if not failure_mode:
        console.print(f"[red]Unknown failure mode:[/red] {failure_mode_id}")
        raise typer.Exit(code=1)

    rpn = failure_mode.calculate_rpn()
    tier = rpn.tier

    result = {
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

    if json_output:
        print(json_module.dumps(result, indent=2))
        return

    console.print(Panel.fit(
        f"[bold blue]Fix: {failure_mode_id}[/bold blue]",
        border_style="blue"
    ))

    console.print(f"\n[bold]Escalation Tier:[/bold] {tier.name} (RPN: {rpn.rpn})")

    if rpn.requires_approval:
        console.print("[yellow]This issue requires manual intervention.[/yellow]")
    elif dry_run:
        console.print("[cyan]DRY RUN - showing what would be done[/cyan]")
    else:
        console.print("[green]Executing remediation...[/green]")

    console.print(f"\n[bold]Remediation Instructions:[/bold]")
    for i, step in enumerate(failure_mode.remediation_steps, 1):
        console.print(f"  {i}. {step}")


@app.command()
def pyflink(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Gather diagnostic information for PyFlink job failures.

    Use this when PyFlink jobs fail with "Python process exits with code: 1"
    or similar Python-related errors.

    Collects:
    - Platform info (macOS vs Linux)
    - Python environment (version, paths, PYTHONPATH)
    - PyFlink/kafka-python installation status
    - Flink configuration (Python settings)
    - Job submission logs
    - TaskManager/JobManager logs (Python-related)

    Examples:
        cybersec health pyflink
        cybersec health pyflink --json
    """
    import json as json_module
    from ..health.pyflink_diagnostics import gather_pyflink_diagnostics

    result = asyncio.run(gather_pyflink_diagnostics())

    if json_output:
        print(json_module.dumps(result, indent=2))
        return

    # Pretty print for humans
    console.print(Panel.fit(
        "[bold blue]PyFlink Diagnostics[/bold blue]",
        border_style="blue"
    ))

    # Platform
    platform_info = result.get("platform", {})
    console.print("\n[bold]Platform:[/bold]")
    console.print(f"  System: {platform_info.get('system', 'unknown')}")
    console.print(f"  Release: {platform_info.get('release', 'unknown')}")
    console.print(f"  Machine: {platform_info.get('machine', 'unknown')}")
    if platform_info.get("is_macos"):
        console.print("  [yellow]macOS detected - check Python path configuration[/yellow]")

    # Python environment
    py_env = result.get("python_environment", {})
    console.print("\n[bold]Python Environment:[/bold]")
    console.print(f"  Version: {py_env.get('version', 'unknown').split()[0]}")
    console.print(f"  Executable: {py_env.get('executable', 'unknown')}")
    console.print(f"  System python3: {py_env.get('system_python3', 'unknown')}")
    console.print(f"  PYTHONPATH: {py_env.get('pythonpath', 'not set')}")

    if py_env.get("pyflink_version"):
        console.print(f"  [green]PyFlink: {py_env.get('pyflink_version')}[/green]")
        console.print(f"    Location: {py_env.get('pyflink_location', 'unknown')}")
    elif py_env.get("pyflink_error"):
        console.print(f"  [red]PyFlink: NOT INSTALLED[/red]")
        console.print(f"    Error: {py_env.get('pyflink_error')}")

    kafka_status = py_env.get("kafka_python", "unknown")
    if kafka_status == "installed":
        console.print(f"  [green]kafka-python: installed[/green]")
    else:
        console.print(f"  [red]kafka-python: {kafka_status}[/red]")

    if py_env.get("devenv_python"):
        console.print(f"  Devenv Python: {py_env.get('devenv_python')}")
        exists = py_env.get("devenv_python_exists", False)
        console.print(f"    Exists: {'[green]yes[/green]' if exists else '[red]no[/red]'}")

    # Flink config
    flink_cfg = result.get("flink_config", {})
    console.print("\n[bold]Flink Configuration:[/bold]")
    console.print(f"  FLINK_HOME: {flink_cfg.get('flink_home', 'not set')}")
    console.print(f"  Exists: {'[green]yes[/green]' if flink_cfg.get('flink_home_exists') else '[red]no[/red]'}")
    console.print(f"  Binary exists: {'[green]yes[/green]' if flink_cfg.get('flink_binary_exists') else '[red]no[/red]'}")

    python_settings = flink_cfg.get("python_settings", [])
    if python_settings and python_settings != ["none configured"]:
        console.print("  Python settings in flink-conf.yaml:")
        for setting in python_settings:
            console.print(f"    {setting}")
    else:
        console.print("  [dim]No Python settings in flink-conf.yaml[/dim]")

    # Logs
    logs = result.get("logs", {})
    console.print("\n[bold]Logs:[/bold]")

    submit_log = logs.get("submit_log")
    if isinstance(submit_log, dict):
        console.print(f"  Submit log: {submit_log.get('path', 'unknown')}")
        console.print(f"    Total lines: {submit_log.get('total_lines', 0)}")
        errors = submit_log.get("recent_errors", [])
        if errors and errors != ["no errors found in recent lines"]:
            console.print("    [red]Recent errors:[/red]")
            for err in errors[-5:]:
                console.print(f"      {err[:100]}...")
        else:
            console.print("    [green]No recent errors[/green]")
    else:
        console.print(f"  Submit log: {submit_log}")

    tm_log = logs.get("taskmanager")
    if isinstance(tm_log, dict):
        console.print(f"  TaskManager log: {tm_log.get('path', 'unknown')}")
        py_lines = tm_log.get("python_related_lines", [])
        if py_lines and py_lines != ["no python-related entries"]:
            console.print("    [yellow]Python-related entries:[/yellow]")
            for line in py_lines[-5:]:
                console.print(f"      {line[:100]}...")

    jm_log = logs.get("jobmanager")
    if isinstance(jm_log, dict):
        console.print(f"  JobManager log: {jm_log.get('path', 'unknown')}")
        py_lines = jm_log.get("python_related_lines", [])
        if py_lines and py_lines != ["no python-related entries"]:
            console.print("    [yellow]Python-related entries:[/yellow]")
            for line in py_lines[-5:]:
                console.print(f"      {line[:100]}...")

    # Recommendations
    recommendations = result.get("recommendations", [])
    if recommendations:
        console.print("\n[bold yellow]Recommendations:[/bold yellow]")
        for rec in recommendations:
            console.print(f"  • {rec}")
