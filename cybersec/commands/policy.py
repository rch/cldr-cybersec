"""Policy commands for unified command system.

Commands:
    /policy check      - Run conftest policy validation
    /policy generate   - Generate hydrated config for conftest
    /policy show       - Show effective configuration
"""

import json
from pathlib import Path

from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_policy_check(cmd: ParsedCommand) -> CommandResult:
    """Run conftest policy validation against current environment.

    Hydrates HOCON config with runtime state and validates against Rego policies.

    Options:
        --env, -e <env>  Environment (local, ci). Default: local
        --json, -j       Output as JSON
    """
    from ..health.environment import run_conftest

    environment = cmd.options.get("env", "local")

    # Generate hydrated config
    config_path = await _hydrate_full_config(environment)

    # Run conftest
    result = run_conftest(config_path)

    formatted = _format_policy_result(result, config_path)

    return CommandResult(
        success=result["success"],
        data=result,
        formatted=formatted,
    )


async def cmd_policy_generate(cmd: ParsedCommand) -> CommandResult:
    """Generate hydrated configuration JSON for conftest.

    Merges HOCON static config with runtime state and writes to build/.

    Options:
        --env, -e <env>      Environment (local, ci). Default: local
        --output, -o <path>  Output file path (default: build/config.json)
        --json, -j           Output as JSON
    """
    environment = cmd.options.get("env", "local")
    output = cmd.options.get("output")
    output_path = Path(output) if output else Path("build/config.json")

    config_path = await _hydrate_full_config(environment, output_path)

    formatted = f"""Configuration generated:
  Environment: {environment}
  Output: {config_path}

Run policy check:
  conftest test {config_path} --policy policy/environment/ --all-namespaces

Or use:
  cybersec --cmd '/policy check'"""

    return CommandResult(
        success=True,
        data={"path": str(config_path), "environment": environment},
        formatted=formatted,
    )


async def cmd_policy_show(cmd: ParsedCommand) -> CommandResult:
    """Show effective configuration for an environment.

    Displays the merged HOCON + runtime configuration.

    Options:
        --env, -e <env>  Environment (local, ci). Default: local
        --path, -p <p>   Show specific config path (e.g., cybersec.services.postgres)
        --json, -j       Output as JSON
    """
    from ..config import load_config, hydrate_config
    from ..config.runtime import gather_runtime_config, merge_runtime_config

    environment = cmd.options.get("env", "local")
    config_path = cmd.options.get("path")

    try:
        static_config = load_config(environment)
        static_dict = hydrate_config(static_config)
    except FileNotFoundError as e:
        return CommandResult(
            success=False,
            error=f"Configuration not found: {e}",
        )

    runtime_config = await gather_runtime_config()
    merged = merge_runtime_config(static_dict, runtime_config)

    if config_path:
        # Navigate to specific path
        value = merged.get("effective", {})
        for part in config_path.split("."):
            if isinstance(value, dict):
                value = value.get(part, {})
            else:
                value = None
                break

        return CommandResult(
            success=True,
            data=value,
            formatted=json.dumps(value, indent=2) if value else f"Path not found: {config_path}",
        )

    # Format full config
    formatted = _format_config_summary(merged, environment)

    return CommandResult(
        success=True,
        data=merged,
        formatted=formatted,
    )


async def _hydrate_full_config(
    environment: str,
    output_path: Path | None = None,
) -> Path:
    """Hydrate HOCON config with runtime state and write to file."""
    import json
    from ..config import load_config, hydrate_config
    from ..config.runtime import gather_runtime_config, merge_runtime_config

    if output_path is None:
        output_path = Path("build/config.json")

    # Load and hydrate static config
    try:
        static_config = load_config(environment)
        static_dict = hydrate_config(static_config)
    except FileNotFoundError:
        # Fall back to runtime-only config if HOCON not found
        static_dict = {}

    # Gather runtime state
    runtime_config = await gather_runtime_config()

    # Merge
    merged = merge_runtime_config(static_dict, runtime_config)

    # Write
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(merged, f, indent=2)

    return output_path


def _format_config_summary(merged: dict, environment: str) -> str:
    """Format configuration summary for human display."""
    lines = []
    lines.append(f"Configuration Summary (environment: {environment})")
    lines.append("=" * 50)

    effective = merged.get("effective", {})

    # Platform
    platform = effective.get("platform", {})
    lines.append("")
    lines.append("Platform:")
    lines.append(f"  System: {platform.get('system', 'unknown')}")
    lines.append(f"  Machine: {platform.get('machine', 'unknown')}")

    # Paths
    paths = effective.get("paths", {})
    lines.append("")
    lines.append("Paths:")
    lines.append(f"  FLINK_HOME: {paths.get('flink_home', 'not set')}")
    lines.append(f"    Exists: {'yes' if paths.get('flink_home_exists') else 'no'}")
    lines.append(f"    Env set: {'yes' if paths.get('flink_home_env_set') else 'no'}")

    # Python
    python = effective.get("python", {})
    lines.append("")
    lines.append("Python:")
    lines.append(f"  Version: {python.get('version', 'unknown')}")
    lines.append(f"  PyFlink: {'installed' if python.get('pyflink_installed') else 'not installed'}")
    lines.append(f"  kafka-python: {'installed' if python.get('kafka_installed') else 'not installed'}")

    # Flink
    flink = effective.get("flink", {})
    lines.append("")
    lines.append("Flink:")
    lines.append(f"  Python configured: {'yes' if flink.get('python_configured') else 'no'}")
    if flink.get('configured_python_path'):
        lines.append(f"  Python path: {flink.get('configured_python_path')}")
        lines.append(f"    Exists: {'yes' if flink.get('configured_python_exists') else 'no'}")
    lines.append(f"  TaskManager running: {'yes' if flink.get('taskmanager_running') else 'no'}")
    if flink.get('process_stale'):
        lines.append("  ⚠ Process stale (config newer than process)")

    # Services
    services = effective.get("services", {})
    lines.append("")
    lines.append("Services:")
    for name, svc in services.items():
        healthy = svc.get("healthy", svc.get("jobmanager_healthy", False))
        icon = "✓" if healthy else "✗"
        lines.append(f"  {icon} {name}")

    return "\n".join(lines)


def _format_policy_result(result: dict, config_path: Path) -> str:
    """Format policy check result for human display."""
    lines = []
    lines.append("Policy Validation")
    lines.append("=" * 40)
    lines.append(f"Config: {config_path}")
    lines.append("")

    if result.get("error"):
        lines.append(f"✗ Error: {result['error']}")
        return "\n".join(lines)

    failures = result.get("failures", [])
    warnings = result.get("warnings", [])

    if failures:
        lines.append("FAILURES:")
        for msg in failures:
            lines.append(f"  ✗ {msg}")
        lines.append("")

    if warnings:
        lines.append("WARNINGS:")
        for msg in warnings:
            lines.append(f"  ⚠ {msg}")
        lines.append("")

    if result["success"]:
        lines.append("✓ All policy checks passed")
    else:
        lines.append(f"✗ {len(failures)} failure(s), {len(warnings)} warning(s)")
        lines.append("")
        lines.append("Fix issues and re-run:")
        lines.append("  cybersec --cmd '/policy check'")

    return "\n".join(lines)


def register_policy_commands():
    """Register all policy commands."""
    register_command(
        "policy.check",
        cmd_policy_check,
        description="Run conftest policy validation",
        options=[
            {"name": "env", "short": "e", "description": "Environment (local, ci)"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/policy check",
            "/policy check --env ci",
            "/policy check --json",
        ],
    )

    register_command(
        "policy.generate",
        cmd_policy_generate,
        description="Generate hydrated config for conftest",
        options=[
            {"name": "env", "short": "e", "description": "Environment (local, ci)"},
            {"name": "output", "short": "o", "description": "Output file path"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/policy generate",
            "/policy generate --env ci",
            "/policy generate --output /tmp/config.json",
        ],
    )

    register_command(
        "policy.show",
        cmd_policy_show,
        description="Show effective configuration",
        options=[
            {"name": "env", "short": "e", "description": "Environment (local, ci)"},
            {"name": "path", "short": "p", "description": "Config path to show"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/policy show",
            "/policy show --path services.postgres",
            "/policy show --env ci --json",
        ],
    )
