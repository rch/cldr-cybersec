"""Command registry for unified command system.

Maintains a registry of all available commands and their handlers.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional
from .parser import ParsedCommand, CommandResult


# Type for command handlers
CommandHandler = Callable[[ParsedCommand], Coroutine[Any, Any, CommandResult]]


@dataclass
class CommandInfo:
    """Information about a registered command."""
    name: str                    # e.g., "health.pyflink"
    handler: CommandHandler      # Async function to execute
    description: str = ""        # Short description
    help_text: str = ""          # Detailed help
    args: list[dict] = field(default_factory=list)     # Argument definitions
    options: list[dict] = field(default_factory=list)  # Option definitions
    examples: list[str] = field(default_factory=list)  # Usage examples


# Global command registry
_commands: dict[str, CommandInfo] = {}


def register_command(
    name: str,
    handler: CommandHandler,
    description: str = "",
    help_text: str = "",
    args: Optional[list[dict]] = None,
    options: Optional[list[dict]] = None,
    examples: Optional[list[str]] = None,
) -> CommandInfo:
    """Register a command handler.

    Args:
        name: Command path like "health.pyflink" or "bootstrap.status"
        handler: Async function that takes ParsedCommand and returns CommandResult
        description: Short one-line description
        help_text: Detailed help text
        args: List of argument definitions [{"name": "id", "required": True, ...}]
        options: List of option definitions [{"name": "json", "short": "j", ...}]
        examples: List of example command strings

    Returns:
        CommandInfo for the registered command
    """
    info = CommandInfo(
        name=name,
        handler=handler,
        description=description,
        help_text=help_text,
        args=args or [],
        options=options or [],
        examples=examples or [],
    )
    _commands[name] = info

    # Also register under parent command if this is a subcommand
    # e.g., "health.pyflink" also registers under "health" for discovery
    if "." in name:
        parent = name.split(".")[0]
        if parent not in _commands:
            # Create a placeholder parent command that lists subcommands
            _commands[parent] = CommandInfo(
                name=parent,
                handler=_list_subcommands_handler(parent),
                description=f"Commands under /{parent}",
            )

    return info


def get_command(name: str) -> Optional[CommandInfo]:
    """Get a command by name.

    Args:
        name: Command path like "health.pyflink" or "health"

    Returns:
        CommandInfo if found, None otherwise
    """
    return _commands.get(name)


def list_commands(prefix: str = "") -> list[CommandInfo]:
    """List all registered commands.

    Args:
        prefix: Optional prefix to filter commands (e.g., "health")

    Returns:
        List of CommandInfo for matching commands
    """
    if not prefix:
        return list(_commands.values())

    return [
        cmd for name, cmd in _commands.items()
        if name.startswith(prefix)
    ]


def get_subcommands(parent: str) -> list[CommandInfo]:
    """Get all subcommands of a parent command.

    Args:
        parent: Parent command name like "health"

    Returns:
        List of CommandInfo for subcommands
    """
    prefix = f"{parent}."
    return [
        cmd for name, cmd in _commands.items()
        if name.startswith(prefix) and name != parent
    ]


def _list_subcommands_handler(parent: str) -> CommandHandler:
    """Create a handler that lists subcommands."""

    async def handler(cmd: ParsedCommand) -> CommandResult:
        subcommands = get_subcommands(parent)
        data = {
            "command": parent,
            "subcommands": [
                {
                    "name": sub.name.split(".")[-1],
                    "description": sub.description,
                }
                for sub in subcommands
            ],
        }

        # Format for human display
        lines = [f"Available subcommands for /{parent}:", ""]
        for sub in subcommands:
            sub_name = sub.name.split(".")[-1]
            lines.append(f"  /{parent} {sub_name:<15} {sub.description}")

        return CommandResult(
            success=True,
            data=data,
            formatted="\n".join(lines),
        )

    return handler
