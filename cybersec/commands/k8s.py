"""K8s commands for unified command system.

Commands:
    /k8s                         - Show K8s status and detected target
    /k8s prepare                 - Prepare target (auto-detect)
    /k8s prepare aws             - Prepare AWS target
    /k8s prepare rke2            - Prepare RKE2 target
    /k8s prepare k3d             - Prepare k3d target
    /k8s validate                - Validate target (auto-detect)
    /k8s validate aws            - Validate AWS target
"""

from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_k8s(cmd: ParsedCommand) -> CommandResult:
    """Show K8s status and detected target.

    Options:
        --json, -j  Output as JSON
    """
    from ..config.runtime import gather_runtime_config
    from ..k8s.prepare import detect_target

    runtime = await gather_runtime_config()
    k8s = runtime.get("kubernetes", {})
    tools = runtime.get("tools", {})
    target = detect_target()

    data = {
        "target": target.value,
        "kubernetes": k8s,
        "tools": {
            "kubectl": tools.get("kubectl", False),
            "helm": tools.get("helm", False),
            "k3d": tools.get("k3d", False),
            "iac_tool": tools.get("iac_tool"),
        },
    }

    # Format for human display
    lines = [
        "K8s Status",
        "=" * 40,
        f"Detected target: {target.value}",
        "",
        "Kubernetes:",
        f"  Enabled:          {k8s.get('enabled', False)}",
        f"  Kubeconfig:       {k8s.get('kubeconfig_path', 'not set')}",
        f"  Kubeconfig exists: {k8s.get('kubeconfig_exists', False)}",
        f"  Cluster type:     {k8s.get('cluster_type', 'none')}",
        f"  Connected:        {k8s.get('kubectl_connected', False)}",
        "",
        "Tools:",
        f"  kubectl:          {'installed' if tools.get('kubectl') else 'not found'}",
        f"  helm:             {'installed' if tools.get('helm') else 'not found'}",
        f"  k3d:              {'installed' if tools.get('k3d') else 'not found'}",
        f"  IaC tool:         {tools.get('iac_tool') or 'not found'}",
    ]

    return CommandResult(
        success=True,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_k8s_prepare(cmd: ParsedCommand) -> CommandResult:
    """Prepare K8s target configuration.

    Usage:
        /k8s prepare            Prepare auto-detected target
        /k8s prepare aws        Prepare AWS target
        /k8s prepare rke2       Prepare RKE2 target
        /k8s prepare k3d        Prepare k3d target

    Options:
        --dry-run   Validate but don't write config
        --json, -j  Output as JSON
    """
    from ..k8s.prepare import prepare_target, K8sTarget

    # Get target from args
    target_str = cmd.args[0] if cmd.args else None
    target = None
    if target_str:
        target_str = target_str.lower()
        if target_str == "aws":
            target = K8sTarget.AWS
        elif target_str == "rke2":
            target = K8sTarget.RKE2
        elif target_str == "k3d":
            target = K8sTarget.K3D
        else:
            return CommandResult(
                success=False,
                error=f"Unknown target: {target_str}. Available: aws, rke2, k3d",
            )

    dry_run = cmd.options.get("dry_run", False)
    result = await prepare_target(target=target, dry_run=dry_run)

    data = {
        "success": result.success,
        "target": result.target.value,
        "validation": {
            "success": result.validation.success,
            "denies": result.validation.denies,
            "warnings": result.validation.warnings,
            "infos": result.validation.infos,
        },
        "config_path": result.config_path,
        "config": result.config,
        "message": result.message,
    }

    # Format for human display
    lines = [
        f"K8s Target Preparation: {result.target.value}",
        "=" * 50,
    ]

    if result.validation.denies:
        lines.append("")
        lines.append("ERRORS:")
        for deny in result.validation.denies:
            lines.append(f"  - {deny}")

    if result.validation.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for warn in result.validation.warnings:
            lines.append(f"  - {warn}")

    if result.validation.infos:
        lines.append("")
        lines.append("INFO:")
        for info in result.validation.infos:
            lines.append(f"  - {info}")

    lines.append("")
    if result.success:
        if dry_run:
            lines.append("DRY RUN: Validation passed. Run without --dry-run to write config.")
        else:
            lines.append(f"Config written to: {result.config_path}")
            lines.append("")
            lines.append("Configuration:")
            for section, values in result.config.items():
                if isinstance(values, dict):
                    lines.append(f"  [{section}]")
                    for k, v in values.items():
                        if isinstance(v, dict):
                            for kk, vv in v.items():
                                lines.append(f"    {kk} = {vv}")
                        else:
                            lines.append(f"    {k} = {v}")
    else:
        lines.append(f"Preparation failed: {result.message}")

    return CommandResult(
        success=result.success,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_k8s_validate(cmd: ParsedCommand) -> CommandResult:
    """Validate K8s target configuration.

    Usage:
        /k8s validate           Validate auto-detected target
        /k8s validate aws       Validate AWS target
        /k8s validate rke2      Validate RKE2 target
        /k8s validate k3d       Validate k3d target

    Options:
        --json, -j  Output as JSON
    """
    from ..k8s.prepare import validate_target, detect_target, K8sTarget

    # Get target from args
    target_str = cmd.args[0] if cmd.args else None
    if target_str:
        target_str = target_str.lower()
        if target_str == "aws":
            target = K8sTarget.AWS
        elif target_str == "rke2":
            target = K8sTarget.RKE2
        elif target_str == "k3d":
            target = K8sTarget.K3D
        else:
            return CommandResult(
                success=False,
                error=f"Unknown target: {target_str}. Available: aws, rke2, k3d",
            )
    else:
        target = detect_target()
        if target == K8sTarget.NONE:
            return CommandResult(
                success=False,
                error="No K8s target detected. Specify: /k8s validate [aws|rke2|k3d]",
            )

    result = await validate_target(target)

    data = {
        "success": result.success,
        "target": result.target.value,
        "denies": result.denies,
        "warnings": result.warnings,
        "infos": result.infos,
    }

    # Format for human display
    lines = [
        f"K8s Target Validation: {result.target.value}",
        "=" * 50,
    ]

    if result.denies:
        lines.append("")
        lines.append("ERRORS:")
        for deny in result.denies:
            lines.append(f"  - {deny}")

    if result.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for warn in result.warnings:
            lines.append(f"  - {warn}")

    if result.infos:
        lines.append("")
        lines.append("INFO:")
        for info in result.infos:
            lines.append(f"  - {info}")

    lines.append("")
    if result.success:
        lines.append("Validation PASSED")
    else:
        lines.append(f"Validation FAILED: {len(result.denies)} error(s)")

    return CommandResult(
        success=result.success,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_k8s_rke2(cmd: ParsedCommand) -> CommandResult:
    """Show RKE2 environment status.

    Displays service status, kubeconfig freshness, and connectivity.

    Options:
        --json, -j  Output as JSON
    """
    from ..k8s.rke2 import get_rke2_status, RKE2Config
    from ..bootstrap import BootstrapService

    try:
        service = BootstrapService()
        config = service.get_config()
        rke2_config = config.get_rke2_config()
    except Exception:
        rke2_config = RKE2Config()

    status = get_rke2_status(rke2_config)

    data = {
        "service_active": status.service_active,
        "service_since": status.service_since.isoformat() if status.service_since else None,
        "system_kubeconfig_exists": status.system_kubeconfig_exists,
        "user_kubeconfig_exists": status.user_kubeconfig_exists,
        "kubeconfig_stale": status.kubeconfig_stale,
        "staleness_seconds": status.staleness_seconds,
        "kubectl_connected": status.kubectl_connected,
        "needs_refresh": status.needs_refresh,
        "refresh_command": status.refresh_command,
    }

    # Format timestamps for display
    def _fmt_mtime(mtime: float | None) -> str:
        if mtime is None:
            return "n/a"
        from datetime import datetime
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "RKE2 Environment Status",
        "=" * 40,
        "",
        "Service:",
        f"  Active:           {'yes' if status.service_active else 'no'}",
        f"  Since:            {status.service_since.strftime('%Y-%m-%d %H:%M:%S') if status.service_since else 'n/a'}",
        "",
        "Kubeconfig:",
        f"  System exists:    {'yes' if status.system_kubeconfig_exists else 'no'}",
        f"  System mtime:     {_fmt_mtime(status.system_kubeconfig_mtime)}",
        f"  User exists:      {'yes' if status.user_kubeconfig_exists else 'no'}",
        f"  User mtime:       {_fmt_mtime(status.user_kubeconfig_mtime)}",
        f"  Stale:            {'YES' if status.kubeconfig_stale else 'no'}",
    ]

    if status.staleness_seconds is not None:
        lines.append(f"  Staleness:        {status.staleness_seconds:.0f}s")

    lines.extend([
        "",
        "Connectivity:",
        f"  kubectl:          {'connected' if status.kubectl_connected else 'NOT connected'}",
        "",
    ])

    if status.needs_refresh:
        lines.extend([
            "ACTION NEEDED: Kubeconfig needs refresh",
            f"  Run: /k8s rke2 refresh --apply",
            f"  Or:  {status.refresh_command}",
        ])
    else:
        lines.append("Status: OK - no action needed")

    return CommandResult(
        success=True,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_k8s_rke2_refresh(cmd: ParsedCommand) -> CommandResult:
    """Refresh user kubeconfig from system copy.

    Copies the system kubeconfig to the user location with correct
    ownership and permissions. Requires sudo.

    Options:
        --apply     Execute the refresh (default is dry-run)
        --json, -j  Output as JSON
    """
    from ..k8s.rke2 import refresh_kubeconfig, RKE2Config
    from ..bootstrap import BootstrapService

    try:
        service = BootstrapService()
        config = service.get_config()
        rke2_config = config.get_rke2_config()
    except Exception:
        rke2_config = RKE2Config()

    apply = cmd.options.get("apply", False)
    result = refresh_kubeconfig(rke2_config, dry_run=not apply)

    data = result

    if result.get("dry_run"):
        lines = [
            "RKE2 Kubeconfig Refresh (dry-run)",
            "=" * 40,
            "",
            f"Command: {result.get('command', 'n/a')}",
            "",
            "Run with --apply to execute.",
        ]
    elif result.get("success"):
        lines = [
            "RKE2 Kubeconfig Refresh",
            "=" * 40,
            "",
            result.get("message", "Done"),
        ]
    else:
        lines = [
            "RKE2 Kubeconfig Refresh FAILED",
            "=" * 40,
            "",
            result.get("message", "Unknown error"),
        ]

    return CommandResult(
        success=result.get("success", False),
        data=data,
        formatted="\n".join(lines),
    )


def register_k8s_commands():
    """Register all K8s commands."""
    register_command(
        "k8s",
        cmd_k8s,
        description="Show K8s status and detected target",
        examples=["/k8s", "/k8s --json"],
    )

    register_command(
        "k8s.prepare",
        cmd_k8s_prepare,
        description="Prepare K8s target configuration",
        args=[{"name": "target", "required": False, "help": "Target: aws, rke2, k3d"}],
        options=[{"name": "dry-run", "help": "Validate but don't write config"}],
        examples=[
            "/k8s prepare",
            "/k8s prepare aws",
            "/k8s prepare rke2 --dry-run",
        ],
    )

    register_command(
        "k8s.validate",
        cmd_k8s_validate,
        description="Validate K8s target configuration",
        args=[{"name": "target", "required": False, "help": "Target: aws, rke2, k3d"}],
        examples=[
            "/k8s validate",
            "/k8s validate aws",
            "/k8s validate k3d --json",
        ],
    )

    register_command(
        "k8s.rke2",
        cmd_k8s_rke2,
        description="Show RKE2 environment status",
        examples=["/k8s rke2", "/k8s rke2 --json"],
    )

    register_command(
        "k8s.rke2.refresh",
        cmd_k8s_rke2_refresh,
        description="Refresh user kubeconfig from system copy",
        options=[{"name": "apply", "help": "Execute refresh (default is dry-run)"}],
        examples=[
            "/k8s rke2 refresh",
            "/k8s rke2 refresh --apply",
        ],
    )
