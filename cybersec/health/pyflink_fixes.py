"""PyFlink fix implementations.

Provides automated fixes for detected PyFlink issues.
Uses FMEA tier system to determine which fixes can be auto-applied.
"""

import os
import platform
import shutil
from pathlib import Path
from typing import Any


async def apply_pyflink_fixes(diagnostics: dict, dry_run: bool = True) -> list[dict[str, Any]]:
    """Apply fixes for detected PyFlink issues.

    Args:
        diagnostics: Output from gather_pyflink_diagnostics()
        dry_run: If True, show what would be done without making changes

    Returns:
        List of fix results with status and details
    """
    from ..bootstrap import BootstrapService

    results: list[dict[str, Any]] = []
    issues = diagnostics.get("issues", [])

    if not issues:
        return results

    service = BootstrapService()
    config = service.get_config()
    flink_home = config.get_flink_home()

    for issue in issues:
        failure_mode_id = issue.get("failure_mode_id", "")

        if failure_mode_id == "PYFLINK_001":
            # PyFlink not installed - can auto-fix
            result = await _fix_pyflink_not_installed(dry_run)
            results.append(result)

        elif failure_mode_id == "PYFLINK_002":
            # Python path mismatch - fix flink-conf.yaml
            result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
            results.append(result)

        elif failure_mode_id == "PYFLINK_003":
            # kafka-python missing - can auto-fix
            result = await _fix_kafka_python_missing(dry_run)
            results.append(result)

        elif failure_mode_id == "PYFLINK_004":
            # FLINK_HOME not set - manual fix required
            results.append({
                "failure_mode_id": "PYFLINK_004",
                "action": "manual_required",
                "success": False,
                "message": "FLINK_HOME not set. Run: devenv tasks run restart:clean",
                "details": "This will build Flink from source if needed.",
            })

        elif failure_mode_id == "PYFLINK_005":
            # macOS Python configuration - same fix as PYFLINK_002
            # Already handled by PYFLINK_002, skip duplicate
            if not any(r.get("failure_mode_id") == "PYFLINK_002" for r in results):
                result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
                result["failure_mode_id"] = "PYFLINK_005"
                results.append(result)

        elif failure_mode_id == "PYFLINK_006":
            # Log errors - informational only
            results.append({
                "failure_mode_id": "PYFLINK_006",
                "action": "review_required",
                "success": True,
                "message": "Log errors detected - review recommended",
                "details": "Check /tmp/cloudtrail_submit.log for specific errors",
            })

        elif failure_mode_id == "PYFLINK_007":
            # Config written but not applied - need cluster restart
            result = await _fix_flink_cluster_restart(flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_007"
            results.append(result)

        elif failure_mode_id == "PYFLINK_008":
            # Flink cluster stale - need cluster restart
            result = await _fix_flink_cluster_restart(flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_008"
            results.append(result)

        elif failure_mode_id == "PYFLINK_009":
            # Python executable not found - re-run config fix
            result = await _fix_python_path_mismatch(diagnostics, flink_home, dry_run)
            result["failure_mode_id"] = "PYFLINK_009"
            result["message"] = "Re-detected Python path and updated flink-conf.yaml"
            results.append(result)

    return results


async def _fix_pyflink_not_installed(dry_run: bool) -> dict[str, Any]:
    """Fix: Install PyFlink package."""
    import subprocess

    result = {
        "failure_mode_id": "PYFLINK_001",
        "action": "install_package",
        "package": "apache-flink",
        "command": "uv pip install apache-flink",
    }

    if dry_run:
        result["success"] = True
        result["message"] = "Would install apache-flink package"
        result["dry_run"] = True
        return result

    try:
        proc = subprocess.run(
            ["uv", "pip", "install", "apache-flink"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )
        if proc.returncode == 0:
            result["success"] = True
            result["message"] = "Installed apache-flink package"
            result["output"] = proc.stdout
        else:
            result["success"] = False
            result["message"] = "Failed to install apache-flink"
            result["error"] = proc.stderr
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error installing package: {e}"

    return result


async def _fix_kafka_python_missing(dry_run: bool) -> dict[str, Any]:
    """Fix: Install kafka-python package."""
    import subprocess

    result = {
        "failure_mode_id": "PYFLINK_003",
        "action": "install_package",
        "package": "kafka-python",
        "command": "uv pip install kafka-python",
    }

    if dry_run:
        result["success"] = True
        result["message"] = "Would install kafka-python package"
        result["dry_run"] = True
        return result

    try:
        proc = subprocess.run(
            ["uv", "pip", "install", "kafka-python"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            result["success"] = True
            result["message"] = "Installed kafka-python package"
            result["output"] = proc.stdout
        else:
            result["success"] = False
            result["message"] = "Failed to install kafka-python"
            result["error"] = proc.stderr
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error installing package: {e}"

    return result


async def _fix_python_path_mismatch(
    diagnostics: dict,
    flink_home: Path | None,
    dry_run: bool
) -> dict[str, Any]:
    """Fix: Update flink-conf.yaml with correct Python path."""

    result = {
        "failure_mode_id": "PYFLINK_002",
        "action": "update_flink_config",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot update config"
        return result

    flink_conf_path = flink_home / "conf" / "flink-conf.yaml"
    if not flink_conf_path.exists():
        result["success"] = False
        result["message"] = f"flink-conf.yaml not found at {flink_conf_path}"
        return result

    # Determine the correct Python path
    py_env = diagnostics.get("python_environment", {})

    # Prefer devenv Python on macOS, otherwise use current executable
    if platform.system() == "Darwin" and py_env.get("devenv_python_exists"):
        python_path = py_env.get("devenv_python")
    else:
        python_path = py_env.get("executable")

    if not python_path:
        result["success"] = False
        result["message"] = "Could not determine correct Python path"
        return result

    result["python_path"] = python_path
    result["config_file"] = str(flink_conf_path)

    # Lines to add
    lines_to_add = [
        f"python.client.executable: {python_path}",
        f"python.executable: {python_path}",
    ]
    result["lines_to_add"] = lines_to_add

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = f"Would add Python configuration to {flink_conf_path}"
        return result

    # Read existing config
    try:
        content = flink_conf_path.read_text()
        lines = content.split("\n")

        # Check if settings already exist
        has_client_exec = any("python.client.executable:" in line and not line.strip().startswith("#") for line in lines)
        has_exec = any("python.executable:" in line and "client" not in line and not line.strip().startswith("#") for line in lines)

        # Backup original
        backup_path = flink_conf_path.with_suffix(".yaml.bak")
        shutil.copy(flink_conf_path, backup_path)
        result["backup"] = str(backup_path)

        # Update or append settings
        new_lines = []
        for line in lines:
            # Skip existing python executable settings (we'll add new ones)
            if "python.client.executable:" in line and not line.strip().startswith("#"):
                continue
            if "python.executable:" in line and "client" not in line and not line.strip().startswith("#"):
                continue
            new_lines.append(line)

        # Add new settings at end (before any trailing empty lines)
        while new_lines and new_lines[-1].strip() == "":
            new_lines.pop()

        new_lines.append("")
        new_lines.append("# PyFlink Python configuration (added by cybersec health fix)")
        for line in lines_to_add:
            new_lines.append(line)
        new_lines.append("")

        # Write updated config
        flink_conf_path.write_text("\n".join(new_lines))

        result["success"] = True
        result["message"] = f"Updated {flink_conf_path} with Python configuration"
        result["restart_required"] = True
        result["restart_command"] = "devenv tasks run restart:clean"

    except Exception as e:
        result["success"] = False
        result["message"] = f"Error updating config: {e}"

    return result


async def _fix_flink_cluster_restart(flink_home: Path | None, dry_run: bool) -> dict[str, Any]:
    """Fix: Restart Flink cluster to apply configuration changes."""
    import subprocess

    result = {
        "action": "restart_flink_cluster",
        "command": "devenv tasks run restart:clean",
    }

    if not flink_home or not flink_home.exists():
        result["success"] = False
        result["message"] = "FLINK_HOME not found - cannot restart cluster"
        return result

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = "Would restart Flink cluster via devenv tasks"
        result["steps"] = [
            f"Stop cluster: {flink_home}/bin/stop-cluster.sh",
            f"Start cluster: {flink_home}/bin/start-cluster.sh",
            "Or: devenv tasks run restart:clean",
        ]
        return result

    # Execute restart using stop/start scripts directly for targeted restart
    try:
        stop_script = flink_home / "bin" / "stop-cluster.sh"
        start_script = flink_home / "bin" / "start-cluster.sh"

        if not stop_script.exists() or not start_script.exists():
            result["success"] = False
            result["message"] = "Flink cluster scripts not found"
            return result

        # Stop cluster
        stop_proc = subprocess.run(
            [str(stop_script)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(flink_home),
        )

        # Small delay to ensure clean shutdown
        import time
        time.sleep(2)

        # Start cluster
        start_proc = subprocess.run(
            [str(start_script)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(flink_home),
        )

        if start_proc.returncode == 0:
            result["success"] = True
            result["message"] = "Flink cluster restarted successfully"
            result["stop_output"] = stop_proc.stdout
            result["start_output"] = start_proc.stdout
        else:
            result["success"] = False
            result["message"] = "Failed to restart Flink cluster"
            result["error"] = start_proc.stderr

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "Timeout restarting Flink cluster"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error restarting cluster: {e}"

    return result
