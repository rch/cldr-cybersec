"""Main CLI entry point using Typer.

All commands go through the unified command parser shared with MCP:

    cybersec "/health fix --apply"
    cybersec "/bootstrap status --json"
    cybersec "/health diagnose FLINK_001"

Architecture Note
-----------------
This CLI is intentionally a thin wrapper around the unified command system in
`cybersec.commands`. All command logic lives there, ensuring parity between:

    - CLI: cybersec "/health fix --apply"
    - MCP: cmd("/health fix --apply")

DO NOT add Typer subcommands or duplicate logic here. To add new functionality:

    1. Add the command handler in cybersec/commands/<domain>.py
    2. Register it in the domain's register_*_commands() function
    3. It automatically becomes available in both CLI and MCP

This pattern ensures:
    - Single implementation of all logic
    - Consistent behavior across interfaces
    - Easier testing (test commands once, works everywhere)
    - Documentation stays in sync (one set of docstrings)

If you find yourself writing @app.command() here, stop and add it to the
unified command system instead.
"""

import asyncio
import typer
from typing import Optional

app = typer.Typer(
    name="cybersec",
    help="Cybersec Toolkit - Security Data Lakehouse CLI",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(
        None,
        help='Command to execute (e.g., "/health fix --apply")',
    ),
    cmd: Optional[str] = typer.Option(
        None,
        "--cmd", "-c",
        help='Command to execute (e.g., "/health fix --apply")',
    ),
):
    """Cybersec Toolkit CLI.

    Run unified commands that work identically across CLI and MCP:

        cybersec "/health"
        cybersec "/health fix --apply"
        cybersec --cmd "/health fix --apply"

    Commands:
        /health                 - Run FMEA health diagnostics
        /health flink           - Check flink category
        /health fix             - Dry-run all detected issues
        /health fix --apply     - Apply all fixes
        /health fix flink       - Dry-run flink category
        /health diagnose <id>   - Diagnose specific failure mode
        /bootstrap status       - Check service health
        /bootstrap run          - Run bootstrap process
        /bootstrap info         - Show configuration
    """
    # Support both positional argument and --cmd option
    cmd_to_run = command or cmd

    if cmd_to_run:
        _run_unified_command(cmd_to_run)
        raise typer.Exit()

    # If no command, show help
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _run_unified_command(command_str: str):
    """Execute a unified command."""
    from ..commands.setup import init_commands
    from ..commands import dispatch, parse_command, CommandError
    from ..commands.parser import OutputFormat
    from ..commands.dispatcher import format_result

    # Initialize command registry
    init_commands()

    # Parse to get output format
    try:
        parsed = parse_command(command_str)
    except CommandError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=1)

    # Dispatch command
    result = asyncio.run(dispatch(parsed))

    # Format and output
    output = format_result(result, parsed.output_format)
    typer.echo(output)

    if not result.success:
        raise typer.Exit(code=1)


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
