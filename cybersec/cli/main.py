"""Main CLI entry point using Typer.

Supports two modes:
1. Subcommand mode: `cybersec bootstrap status`
2. Unified command mode: `cybersec --cmd "/bootstrap status"`

The --cmd mode uses the unified command parser that is shared with MCP and TUI.
"""

import asyncio
import typer
from typing import Optional

app = typer.Typer(
    name="cybersec",
    help="Cybersec Toolkit - Security Data Lakehouse CLI",
    invoke_without_command=True,
)

# Import legacy subcommands (kept for backwards compatibility during transition)
from . import bootstrap
from . import health

# Register legacy subcommands
app.add_typer(bootstrap.app, name="bootstrap")
app.add_typer(health.app, name="health")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    cmd: Optional[str] = typer.Option(
        None,
        "--cmd", "-c",
        help='Execute a unified command (e.g., "/health pyflink --json")',
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
):
    """Cybersec Toolkit CLI.

    Use --cmd to run unified commands that work identically across CLI, MCP, and TUI:

        cybersec --cmd "/health pyflink"
        cybersec --cmd "/bootstrap status --json"
        cybersec -c "/health diagnose FLINK_001"

    Or use subcommands for backwards compatibility:

        cybersec health pyflink
        cybersec bootstrap status
    """
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    if cmd:
        # Unified command mode
        _run_unified_command(cmd)
        raise typer.Exit()

    # If no command and no subcommand invoked, show help
    if ctx.invoked_subcommand is None and not cmd:
        # Show available commands
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


@app.command("cmd")
def cmd_command(
    command: str = typer.Argument(..., help='Command to execute (e.g., "/health pyflink")'),
):
    """Execute a unified command.

    Alternative to --cmd flag for running unified commands:

        cybersec cmd "/health pyflink"
        cybersec cmd "/bootstrap status --json"
    """
    _run_unified_command(command)


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
