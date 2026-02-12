"""Zarf commands for unified command system.

Commands:
    /zarf                    - Show Zarf package info and status
    /zarf preflight          - Validate air-gap deployment requirements
    /zarf package            - Build Zarf package
    /zarf deploy             - Deploy to cluster
"""

from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_zarf(cmd: ParsedCommand) -> CommandResult:
    """Show Zarf package info and status.

    Options:
        --json, -j  Output as JSON
    """
    from ..zarf.preflight import get_package_info

    info = get_package_info()

    if "error" in info:
        return CommandResult(
            success=False,
            error=info["error"],
        )

    data = {
        "package": info,
        "status": "ready",
    }

    # Format for human display
    lines = [
        "Zarf Package: cybersec-dask",
        "=" * 50,
        f"Version: {info.get('version', 'unknown')}",
        f"Description: {info.get('description', '')}",
        "",
        "Components:",
    ]

    for comp in info.get("components", []):
        required = "required" if comp.get("required") else "optional"
        default = "(default: on)" if comp.get("default", True) else "(default: off)"
        lines.append(f"  - {comp.get('name')}: {comp.get('description', '')} [{required}] {default}")

    lines.extend([
        "",
        "Variables:",
    ])

    for var in info.get("variables", []):
        sensitive = " (sensitive)" if var.get("sensitive") else ""
        default = var.get("default", "")
        default_str = f" = {default}" if default and not var.get("sensitive") else ""
        lines.append(f"  - {var.get('name')}{default_str}{sensitive}")

    lines.extend([
        "",
        "Commands:",
        "  /zarf preflight     Validate requirements",
        "  /zarf package       Build package",
        "  /zarf deploy        Deploy to cluster",
    ])

    return CommandResult(
        success=True,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_zarf_preflight(cmd: ParsedCommand) -> CommandResult:
    """Validate air-gap deployment requirements.

    Usage:
        /zarf preflight              Run all preflight checks
        /zarf preflight --registry   Include registry connectivity check

    Options:
        --registry    Check registry connectivity
        --json, -j    Output as JSON
    """
    from ..zarf.preflight import run_preflight_checks

    check_registry = cmd.options.get("registry", False)
    registry_url = cmd.args[0] if cmd.args else None

    result = await run_preflight_checks(
        check_registry=check_registry,
        registry_url=registry_url,
    )

    data = {
        "success": result.success,
        "checks": result.checks,
        "denies": result.denies,
        "warnings": result.warnings,
        "infos": result.infos,
    }

    # Format for human display
    lines = [
        "Zarf Preflight Validation",
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
        lines.append("Preflight PASSED - Ready for Zarf deployment")
        lines.append("")
        lines.append("Next steps:")
        lines.append("  1. Build image: devenv tasks run zarf:image")
        lines.append("  2. Create package: devenv tasks run zarf:package")
        lines.append("  3. Deploy: zarf package deploy zarf-package-cybersec-dask-*.tar.zst")
    else:
        lines.append(f"Preflight FAILED - {len(result.denies)} error(s) must be resolved")

    return CommandResult(
        success=result.success,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_zarf_package(cmd: ParsedCommand) -> CommandResult:
    """Build Zarf package.

    Usage:
        /zarf package            Build package (requires image first)
        /zarf package --confirm  Skip confirmation prompt

    Options:
        --confirm     Skip confirmation
        --json, -j    Output as JSON
    """
    import subprocess
    from pathlib import Path

    # Find zarf directory
    project_root = Path(__file__).parent.parent.parent
    zarf_dir = project_root / "zarf"

    if not (zarf_dir / "zarf.yaml").exists():
        return CommandResult(
            success=False,
            error=f"zarf.yaml not found in {zarf_dir}",
        )

    # Check if custom image exists
    try:
        proc = subprocess.run(
            ["podman", "images", "-q", "cybersec-dask:2024.8.0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not proc.stdout.strip():
            # Try docker
            proc = subprocess.run(
                ["docker", "images", "-q", "cybersec-dask:2024.8.0"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not proc.stdout.strip():
                return CommandResult(
                    success=False,
                    error="Custom image cybersec-dask:2024.8.0 not found. Build it first: devenv tasks run zarf:image",
                )
    except Exception:
        pass  # Continue anyway

    confirm = cmd.options.get("confirm", False)
    confirm_flag = "--confirm" if confirm else ""

    # Build zarf package
    try:
        proc = subprocess.run(
            ["zarf", "package", "create", confirm_flag],
            cwd=zarf_dir,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes
        )

        if proc.returncode == 0:
            # Find created package
            packages = list(zarf_dir.glob("zarf-package-cybersec-dask-*.tar.zst"))
            package_name = packages[0].name if packages else "zarf-package-cybersec-dask-*.tar.zst"

            return CommandResult(
                success=True,
                data={
                    "package": package_name,
                    "output": proc.stdout,
                },
                formatted=f"Package created: {package_name}\n\nDeploy with:\n  zarf package deploy {zarf_dir}/{package_name}",
            )
        else:
            return CommandResult(
                success=False,
                error=f"Package creation failed:\n{proc.stderr}",
            )
    except subprocess.TimeoutExpired:
        return CommandResult(
            success=False,
            error="Package creation timed out after 10 minutes",
        )
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Package creation error: {e}",
        )


async def cmd_zarf_deploy(cmd: ParsedCommand) -> CommandResult:
    """Deploy Zarf package to cluster.

    Usage:
        /zarf deploy                 Deploy package (interactive)
        /zarf deploy --confirm       Skip confirmation prompt
        /zarf deploy <package.zst>   Deploy specific package file

    Options:
        --confirm     Skip confirmation
        --json, -j    Output as JSON
    """
    import subprocess
    from pathlib import Path

    # Find package
    project_root = Path(__file__).parent.parent.parent
    zarf_dir = project_root / "zarf"

    package_path = None
    if cmd.args:
        # Use provided package path
        package_path = Path(cmd.args[0])
        if not package_path.exists():
            # Try relative to zarf dir
            package_path = zarf_dir / cmd.args[0]
    else:
        # Find most recent package
        packages = sorted(zarf_dir.glob("zarf-package-cybersec-dask-*.tar.zst"), reverse=True)
        if packages:
            package_path = packages[0]

    if not package_path or not package_path.exists():
        return CommandResult(
            success=False,
            error="No Zarf package found. Build one first: /zarf package",
        )

    confirm = cmd.options.get("confirm", False)
    confirm_flag = "--confirm" if confirm else ""

    # Deploy package
    try:
        proc = subprocess.run(
            ["zarf", "package", "deploy", str(package_path), confirm_flag],
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minutes
        )

        if proc.returncode == 0:
            return CommandResult(
                success=True,
                data={
                    "package": package_path.name,
                    "output": proc.stdout,
                },
                formatted=f"Package deployed: {package_path.name}\n\nVerify with:\n  kubectl get pods -n dask\n  kubectl get pods -n jupyterhub\n  kubectl get pods -n panel-viz",
            )
        else:
            return CommandResult(
                success=False,
                error=f"Deployment failed:\n{proc.stderr}",
            )
    except subprocess.TimeoutExpired:
        return CommandResult(
            success=False,
            error="Deployment timed out after 30 minutes",
        )
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Deployment error: {e}",
        )


def register_zarf_commands():
    """Register all Zarf commands."""
    register_command(
        "zarf",
        cmd_zarf,
        description="Show Zarf package info and status",
        examples=["/zarf", "/zarf --json"],
    )

    register_command(
        "zarf.preflight",
        cmd_zarf_preflight,
        description="Validate air-gap deployment requirements",
        options=[{"name": "registry", "help": "Check registry connectivity"}],
        examples=[
            "/zarf preflight",
            "/zarf preflight --registry",
            "/zarf preflight --json",
        ],
    )

    register_command(
        "zarf.package",
        cmd_zarf_package,
        description="Build Zarf package",
        options=[{"name": "confirm", "help": "Skip confirmation prompt"}],
        examples=[
            "/zarf package",
            "/zarf package --confirm",
        ],
    )

    register_command(
        "zarf.deploy",
        cmd_zarf_deploy,
        description="Deploy Zarf package to cluster",
        args=[{"name": "package", "required": False, "help": "Package file path"}],
        options=[{"name": "confirm", "help": "Skip confirmation prompt"}],
        examples=[
            "/zarf deploy",
            "/zarf deploy --confirm",
            "/zarf deploy zarf-package-cybersec-dask-v1.0.0.tar.zst",
        ],
    )
