"""Policy commands for unified command system.

Commands:
    /policy check      - Run conftest policy validation
    /policy generate   - Generate environment config for conftest
"""

from pathlib import Path

from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_policy_check(cmd: ParsedCommand) -> CommandResult:
    """Run conftest policy validation against current environment.

    Generates environment config and validates against Rego policies.

    Options:
        --json, -j  Output as JSON
    """
    from ..health.environment import write_environment_config, run_conftest

    # Generate environment config
    config_path = await write_environment_config()

    # Run conftest
    result = run_conftest(config_path)

    formatted = _format_policy_result(result, config_path)

    return CommandResult(
        success=result["success"],
        data=result,
        formatted=formatted,
    )


async def cmd_policy_generate(cmd: ParsedCommand) -> CommandResult:
    """Generate environment configuration JSON for conftest.

    Writes build/environment.json with current environment state.

    Options:
        --output, -o <path>  Output file path (default: build/environment.json)
        --json, -j           Output as JSON
    """
    from ..health.environment import write_environment_config, gather_environment_config

    output = cmd.options.get("output")
    output_path = Path(output) if output else None

    config_path = await write_environment_config(output_path)

    # Also return the config data
    config = await gather_environment_config()

    formatted = f"Environment config written to: {config_path}\n\nRun policy check:\n  conftest test {config_path} --policy policy/environment/"

    return CommandResult(
        success=True,
        data={"path": str(config_path), "config": config},
        formatted=formatted,
    )


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
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/policy check",
            "/policy check --json",
        ],
    )

    register_command(
        "policy.generate",
        cmd_policy_generate,
        description="Generate environment config for conftest",
        options=[
            {"name": "output", "short": "o", "description": "Output file path"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/policy generate",
            "/policy generate --output /tmp/env.json",
        ],
    )
