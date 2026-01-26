"""Unified command system for cybersec toolkit.

This module provides a single command parser that all interfaces delegate to:
- CLI: `cybersec --cmd "/health pyflink --json"`
- MCP: `cmd("/health pyflink --json")`
- TUI: `/health pyflink --json`

Commands follow a slash-prefixed structure:
    /health                     - Run FMEA health diagnostics
    /health pyflink             - PyFlink diagnostics
    /health fix                 - Dry-run all detected issues
    /health fix --apply         - Apply all fixes
    /health fix pyflink         - Dry-run pyflink category
    /health fix system --apply  - Fix system category
    /health diagnose <id>       - Diagnose specific failure mode
    /bootstrap status           - Check service health
    /bootstrap run              - Run bootstrap process
    /bootstrap info             - Show configuration
    /bootstrap verify           - Verify environment
    /bootstrap assess           - Quick assessment

All commands return a CommandResult with structured data and optional
formatted output for human-readable display.
"""

from .parser import parse_command, CommandResult, CommandError
from .registry import register_command, get_command, list_commands
from .dispatcher import dispatch

__all__ = [
    "parse_command",
    "dispatch",
    "CommandResult",
    "CommandError",
    "register_command",
    "get_command",
    "list_commands",
]
