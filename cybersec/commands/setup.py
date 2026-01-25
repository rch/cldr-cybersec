"""Command registration setup.

Call init_commands() once at startup to register all commands.
"""

_initialized = False


def init_commands():
    """Initialize and register all commands.

    Safe to call multiple times - only initializes once.
    """
    global _initialized
    if _initialized:
        return

    from .health import register_health_commands
    from .bootstrap import register_bootstrap_commands

    register_health_commands()
    register_bootstrap_commands()

    _initialized = True
