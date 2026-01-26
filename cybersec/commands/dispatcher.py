"""Command dispatcher for unified command system.

Routes parsed commands to their handlers.
"""

import json
from typing import Optional

from .parser import ParsedCommand, CommandResult, CommandError, OutputFormat, parse_command
from .registry import get_command, list_commands


async def dispatch(command_input: str | ParsedCommand) -> CommandResult:
    """Dispatch a command to its handler.

    Args:
        command_input: Either a command string or ParsedCommand

    Returns:
        CommandResult from the handler

    Raises:
        CommandError: If command is not found or execution fails
    """
    # Parse if string
    if isinstance(command_input, str):
        try:
            cmd = parse_command(command_input)
        except CommandError as e:
            return CommandResult(
                success=False,
                error=f"Parse error: {e.message}",
            )
    else:
        cmd = command_input

    # Find handler - try progressively shorter paths
    # e.g., for "health fix pyflink", try:
    #   1. health.fix.pyflink (if first arg could be sub-subcommand)
    #   2. health.fix (with pyflink as arg)
    #   3. health (with fix as arg)

    handler_info = None

    # First, check if first arg is actually a sub-subcommand
    if cmd.args and cmd.subcommand:
        nested_path = f"{cmd.command}.{cmd.subcommand}.{cmd.args[0]}"
        handler_info = get_command(nested_path)
        if handler_info:
            # Remove the sub-subcommand from args
            cmd.args = cmd.args[1:]

    # Try the parsed full path
    if not handler_info:
        handler_info = get_command(cmd.full_path)

    # If no specific subcommand handler, try parent command with subcommand as first arg
    if not handler_info and cmd.subcommand:
        parent_handler = get_command(cmd.command)
        if parent_handler:
            # Check if this is a registered subcommand or should be treated as arg
            from .registry import get_subcommands
            valid_subs = [s.name.split(".")[-1] for s in get_subcommands(cmd.command)]

            if cmd.subcommand in valid_subs:
                # It's a valid subcommand but we didn't find it - shouldn't happen
                return CommandResult(
                    success=False,
                    error=f"Unknown subcommand '{cmd.subcommand}'. "
                          f"Available: {', '.join(valid_subs)}",
                )
            else:
                # Not a subcommand - treat it as an argument to parent command
                handler_info = parent_handler
                # Prepend subcommand to args so parent can handle it
                cmd.args = [cmd.subcommand] + cmd.args
                cmd.subcommand = None

    if not handler_info:
        # List available commands in error
        available = [c.name for c in list_commands() if "." not in c.name]
        return CommandResult(
            success=False,
            error=f"Unknown command '/{cmd.command}'. "
                  f"Available commands: {', '.join(available)}",
        )

    # Execute handler
    try:
        result = await handler_info.handler(cmd)
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Execution error: {str(e)}",
        )

    return result


def format_result(result: CommandResult, output_format: OutputFormat) -> str:
    """Format a CommandResult for output.

    Args:
        result: The command result
        output_format: Desired output format

    Returns:
        Formatted string
    """
    if output_format == OutputFormat.JSON:
        return json.dumps(result.to_dict(), indent=2)

    if output_format == OutputFormat.HUMAN:
        if result.formatted:
            return result.formatted
        if result.error:
            return f"Error: {result.error}"
        if result.message:
            return result.message
        return json.dumps(result.data, indent=2)

    # PLAIN format
    if result.error:
        return f"Error: {result.error}"
    if result.message:
        return result.message
    return str(result.data)
