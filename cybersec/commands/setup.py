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
    from .policy import register_policy_commands
    from .schema import register_schema_commands
    from .benchmark import register_benchmark_commands
    from .cost import register_cost_commands
    from .k8s import register_k8s_commands
    from .aws import register_aws_commands

    register_health_commands()
    register_bootstrap_commands()
    register_policy_commands()
    register_schema_commands()
    register_benchmark_commands()
    register_cost_commands()
    register_k8s_commands()
    register_aws_commands()

    _initialized = True
