"""Zarf commands for unified command system.

Commands:
    /zarf                    - Show Zarf package info and status
    /zarf preflight          - Validate air-gap deployment requirements
    /zarf package            - Build Zarf package
    /zarf deploy             - Deploy to cluster
    /zarf local              - Show local deployment info
    /zarf local preflight    - Validate local deployment requirements
    /zarf local status       - Show local deployment status
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


async def cmd_zarf_local(cmd: ParsedCommand) -> CommandResult:
    """Show local Zarf deployment info.

    Usage:
        /zarf local              Show local deployment configuration
        /zarf local --json       Output as JSON

    Options:
        --json, -j    Output as JSON
    """
    from ..zarf.local import gather_local_zarf_config

    try:
        config = await gather_local_zarf_config()
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to gather local config: {e}",
        )

    k8s = config.get("kubernetes", {})
    nodes = config.get("node_resources", {})
    zarf_local = config.get("zarf_local", {})
    tools = config.get("tools", {})

    lines = [
        "Local Zarf Deployment Info",
        "=" * 50,
        "",
        "Cluster:",
        f"  Target: {k8s.get('target', 'none')}",
        f"  Type: {k8s.get('cluster_type', 'none')}",
        f"  Connected: {k8s.get('kubectl_connected', False)}",
        f"  KUBECONFIG: {k8s.get('kubeconfig_path', 'not set')}",
        "",
        "Node Resources:",
        f"  Total RAM: {nodes.get('total_memory_gb', 0)} GB",
        f"  Disk Free: {nodes.get('disk_free_pct', 0)}%",
        f"  Disk Pressure: {nodes.get('disk_pressure', False)}",
        f"  Storage Classes: {', '.join(nodes.get('storage_classes', [])) or 'none'}",
        f"  Default SC: {nodes.get('default_storage_class', 'none')}",
        "",
        "Zarf Local Config:",
        f"  Init Package: {'found' if zarf_local.get('init_package_exists') else 'not found'}",
        f"  Deploy Package: {'found' if zarf_local.get('deploy_package_exists') else 'not found'}",
        f"  Worker Replicas: {zarf_local.get('worker_replicas', 2)}",
        f"  Spill Dir: {zarf_local.get('spill_dir') or 'emptyDir (disk-light)'}",
        f"  Registry PVC: {zarf_local.get('registry_pvc_enabled', False)}",
        "",
        "Tools:",
        f"  zarf: {'installed' if tools.get('zarf') else 'not found'}"
        + (f" ({tools.get('zarf_version', '')})" if tools.get('zarf_version') else ""),
        f"  kubectl: {'installed' if tools.get('kubectl') else 'not found'}",
        f"  helm: {'installed' if tools.get('helm') else 'not found'}",
        "",
        "Commands:",
        "  /zarf local preflight    Validate requirements",
        "  /zarf local status       Show deployment status",
        "  devenv tasks run zarf:local:preflight",
        "  devenv tasks run zarf:local:init",
        "  devenv tasks run zarf:local:deploy",
        "  devenv tasks run zarf:local:status",
    ]

    return CommandResult(
        success=True,
        data=config,
        formatted="\n".join(lines),
    )


async def cmd_zarf_local_preflight(cmd: ParsedCommand) -> CommandResult:
    """Validate local Zarf deployment requirements.

    Runs conftest policies against gathered runtime config to validate
    that the local environment is ready for Zarf deployment.

    Usage:
        /zarf local preflight       Run local preflight checks
        /zarf local preflight --json Output as JSON

    Options:
        --json, -j    Output as JSON
    """
    import json
    import subprocess
    from pathlib import Path

    from ..zarf.local import gather_local_zarf_config

    try:
        config = await gather_local_zarf_config()
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to gather local config: {e}",
        )

    # Write environment.json for conftest
    build_dir = Path("build")
    build_dir.mkdir(exist_ok=True)
    env_file = build_dir / "environment.json"
    env_file.write_text(json.dumps(config, indent=2))

    # Run conftest
    project_root = Path(__file__).parent.parent.parent
    conftest_cmd = [
        "conftest", "test", str(env_file),
        "--policy", str(project_root / "policy" / "k8s" / "base.rego"),
        "--policy", str(project_root / "policy" / "k8s" / "local"),
        "--all-namespaces",
        "--output", "json",
    ]

    denies: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    try:
        proc = subprocess.run(
            conftest_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.stdout:
            try:
                output = json.loads(proc.stdout)
                for item in output:
                    for failure in item.get("failures", []):
                        denies.append(failure.get("msg", str(failure)))
                    for warning in item.get("warnings", []):
                        warnings.append(warning.get("msg", str(warning)))
            except json.JSONDecodeError:
                for line in proc.stdout.split("\n"):
                    if "FAIL" in line:
                        denies.append(line)
                    elif "WARN" in line:
                        warnings.append(line)
    except FileNotFoundError:
        denies.append("conftest not installed. Install: brew install conftest")
    except Exception as e:
        denies.append(f"conftest error: {e}")

    success = len(denies) == 0

    data = {
        "success": success,
        "config": config,
        "denies": denies,
        "warnings": warnings,
        "infos": infos,
    }

    lines = [
        "Local Zarf Preflight Validation",
        "=" * 50,
    ]

    if denies:
        lines.append("")
        lines.append("ERRORS:")
        for deny in denies:
            lines.append(f"  - {deny}")

    if warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for warn in warnings:
            lines.append(f"  - {warn}")

    lines.append("")
    if success:
        lines.append("Preflight PASSED - Ready for local Zarf deployment")
        lines.append("")
        lines.append("Next steps:")
        lines.append("  devenv tasks run zarf:local:init")
        lines.append("  devenv tasks run zarf:local:deploy")
    else:
        lines.append(f"Preflight FAILED - {len(denies)} error(s) must be resolved")

    return CommandResult(
        success=success,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_zarf_local_status(cmd: ParsedCommand) -> CommandResult:
    """Show local Zarf deployment status.

    Checks pod status in zarf, dask, jupyterhub, and panel-viz namespaces,
    and verifies service accessibility.

    Usage:
        /zarf local status       Show deployment status
        /zarf local status --json Output as JSON

    Options:
        --json, -j    Output as JSON
    """
    import subprocess

    namespaces = {
        "zarf": "Zarf Registry",
        "dask-operator": "Dask Operator",
        "dask": "Dask Cluster",
        "jupyterhub": "JupyterHub",
        "panel-viz": "Panel-Viz",
    }

    data: dict = {"namespaces": {}, "services": {}}
    lines = [
        "Local Zarf Deployment Status",
        "=" * 50,
    ]

    for ns, label in namespaces.items():
        lines.append(f"\n{label} ({ns}):")
        try:
            proc = subprocess.run(
                ["kubectl", "get", "pods", "-n", ns, "--no-headers"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pods = proc.stdout.strip()
                data["namespaces"][ns] = {"exists": True, "pods": pods}
                for line in pods.split("\n"):
                    lines.append(f"  {line}")
            else:
                data["namespaces"][ns] = {"exists": False}
                lines.append("  (namespace not found or no pods)")
        except Exception:
            data["namespaces"][ns] = {"exists": False, "error": "kubectl failed"}
            lines.append("  (kubectl not available)")

    # Check service accessibility
    lines.append("\nService Accessibility:")
    for name, port in [("Panel-Viz", 30506), ("Dask Dashboard", 30087)]:
        try:
            proc = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"http://localhost:{port}/"],
                capture_output=True, text=True, timeout=5,
            )
            code = proc.stdout.strip()
            accessible = code in ("200", "302")
            data["services"][name] = {"port": port, "accessible": accessible, "http_code": code}
            status = f"OK (HTTP {code})" if accessible else f"not accessible (HTTP {code})"
            lines.append(f"  {name}: http://0.0.0.0:{port}/ - {status}")
        except Exception:
            data["services"][name] = {"port": port, "accessible": False}
            lines.append(f"  {name}: http://0.0.0.0:{port}/ - not accessible")

    return CommandResult(
        success=True,
        data=data,
        formatted="\n".join(lines),
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

    # Local deployment commands
    register_command(
        "zarf.local",
        cmd_zarf_local,
        description="Show local Zarf deployment info",
        examples=["/zarf local", "/zarf local --json"],
    )

    register_command(
        "zarf.local.preflight",
        cmd_zarf_local_preflight,
        description="Validate local deployment requirements",
        examples=[
            "/zarf local preflight",
            "/zarf local preflight --json",
        ],
    )

    register_command(
        "zarf.local.status",
        cmd_zarf_local_status,
        description="Show local Zarf deployment status",
        examples=[
            "/zarf local status",
            "/zarf local status --json",
        ],
    )
