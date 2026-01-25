"""Command parser for unified command system.

Parses slash-prefixed commands into structured command invocations.
"""

import shlex
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class OutputFormat(Enum):
    """Output format for command results."""
    HUMAN = "human"  # Rich formatted output
    JSON = "json"    # JSON output
    PLAIN = "plain"  # Plain text (no formatting)


@dataclass
class ParsedCommand:
    """A parsed command ready for dispatch."""
    command: str           # e.g., "health", "bootstrap"
    subcommand: str = ""   # e.g., "pyflink", "status"
    args: list[str] = field(default_factory=list)  # Positional args
    options: dict[str, Any] = field(default_factory=dict)  # --flag values
    output_format: OutputFormat = OutputFormat.HUMAN
    raw: str = ""          # Original command string

    @property
    def full_path(self) -> str:
        """Return the full command path like 'health.pyflink'."""
        if self.subcommand:
            return f"{self.command}.{self.subcommand}"
        return self.command


@dataclass
class CommandResult:
    """Result from executing a command."""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    formatted: str = ""  # Pre-formatted output for human display
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "data": self.data,
        }
        if self.message:
            result["message"] = self.message
        if self.error:
            result["error"] = self.error
        return result


class CommandError(Exception):
    """Error during command parsing or execution."""

    def __init__(self, message: str, command: str = ""):
        self.message = message
        self.command = command
        super().__init__(message)


def parse_command(command_str: str) -> ParsedCommand:
    """Parse a command string into a ParsedCommand.

    Args:
        command_str: Command string like "/health pyflink --json"

    Returns:
        ParsedCommand with parsed components

    Raises:
        CommandError: If command format is invalid
    """
    command_str = command_str.strip()

    if not command_str:
        raise CommandError("Empty command")

    # Handle slash prefix (optional but canonical)
    if command_str.startswith("/"):
        command_str = command_str[1:]

    if not command_str:
        raise CommandError("Empty command after slash")

    # Parse using shlex for proper quote handling
    try:
        tokens = shlex.split(command_str)
    except ValueError as e:
        raise CommandError(f"Invalid command syntax: {e}")

    if not tokens:
        raise CommandError("No command specified")

    # Extract command and subcommand
    command = tokens[0].lower()
    subcommand = ""
    args: list[str] = []
    options: dict[str, Any] = {}
    output_format = OutputFormat.HUMAN

    # Parse remaining tokens
    i = 1
    while i < len(tokens):
        token = tokens[i]

        if token.startswith("--"):
            # Long option
            opt_name = token[2:]

            # Handle --no-* negation
            if opt_name.startswith("no-"):
                options[opt_name[3:].replace("-", "_")] = False
                i += 1
                continue

            # Handle --json shorthand
            if opt_name == "json":
                output_format = OutputFormat.JSON
                options["json"] = True
                i += 1
                continue

            # Handle --option=value
            if "=" in opt_name:
                key, value = opt_name.split("=", 1)
                options[key.replace("-", "_")] = _parse_value(value)
                i += 1
                continue

            # Handle --option value or --flag
            opt_key = opt_name.replace("-", "_")
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                options[opt_key] = _parse_value(tokens[i + 1])
                i += 2
            else:
                options[opt_key] = True
                i += 1

        elif token.startswith("-") and len(token) == 2:
            # Short option like -j, -q
            opt_char = token[1]

            # Known short options
            if opt_char == "j":
                output_format = OutputFormat.JSON
                options["json"] = True
            elif opt_char == "q":
                options["quick"] = True
            elif opt_char == "c" and i + 1 < len(tokens):
                options["category"] = tokens[i + 1]
                i += 1
            elif opt_char == "f" and i + 1 < len(tokens):
                options["flink_path"] = tokens[i + 1]
                i += 1
            else:
                # Unknown short option, treat as flag
                options[opt_char] = True
            i += 1

        elif not subcommand and not args:
            # First non-option token after command is subcommand
            subcommand = token.lower()
            i += 1
        else:
            # Positional argument
            args.append(token)
            i += 1

    return ParsedCommand(
        command=command,
        subcommand=subcommand,
        args=args,
        options=options,
        output_format=output_format,
        raw=f"/{command}" + (f" {subcommand}" if subcommand else "") +
            (" " + " ".join(args) if args else ""),
    )


def _parse_value(value: str) -> Any:
    """Parse a string value into appropriate type."""
    # Boolean
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    # String
    return value
