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

    # Find handler
    handler_info = get_command(cmd.full_path)

    # If no specific subcommand handler, try parent command
    if not handler_info and cmd.subcommand:
        handler_info = get_command(cmd.command)
        # If parent exists but subcommand doesn't, it's an error
        if handler_info and cmd.subcommand:
            # Check if this is a real subcommand or unknown
            from .registry import get_subcommands
            valid_subs = [s.name.split(".")[-1] for s in get_subcommands(cmd.command)]
            if cmd.subcommand not in valid_subs and valid_subs:
                return CommandResult(
                    success=False,
                    error=f"Unknown subcommand '{cmd.subcommand}'. "
                          f"Available: {', '.join(valid_subs)}",
                )

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
