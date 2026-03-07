"""RKE2 local environment management.

Detects stale kubeconfig after RKE2 restarts and provides refresh capabilities.
RKE2 regenerates /etc/rancher/rke2/rke2.yaml on restart, but the user copy
at ~/.kube/rke2.yaml is not auto-refreshed, causing silent kubectl failures.

Detection strategies:
- Primary: stat(system).st_mtime > stat(user).st_mtime
- Fallback: systemctl ActiveEnterTimestamp > stat(user).st_mtime
"""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RKE2Config:
    """Configuration for RKE2 environment management."""

    system_kubeconfig: str = "/etc/rancher/rke2/rke2.yaml"
    user_kubeconfig: str = "~/.kube/rke2.yaml"
    service_name: str = "rke2-server"
    auto_refresh: bool = False


@dataclass
class RKE2Status:
    """Full RKE2 environment status."""

    service_active: bool
    service_since: datetime | None
    system_kubeconfig_exists: bool
    system_kubeconfig_mtime: float | None
    user_kubeconfig_exists: bool
    user_kubeconfig_mtime: float | None
    kubeconfig_stale: bool
    staleness_seconds: float | None
    kubectl_connected: bool
    needs_refresh: bool
    refresh_command: str


def detect_rke2_service(config: RKE2Config | None = None) -> dict[str, Any]:
    """Detect RKE2 service status via systemctl.

    Returns dict with:
        active: bool - whether service is active
        since: datetime | None - when service last started
        error: str | None - error message if detection failed
    """
    if config is None:
        config = RKE2Config()

    result: dict[str, Any] = {"active": False, "since": None, "error": None}

    try:
        proc = subprocess.run(
            ["systemctl", "is-active", config.service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result["active"] = proc.stdout.strip() == "active"
    except FileNotFoundError:
        result["error"] = "systemctl not found"
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "systemctl timed out"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result

    if result["active"]:
        try:
            proc = subprocess.run(
                [
                    "systemctl", "show",
                    "--property=ActiveEnterTimestamp",
                    config.service_name,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Output: ActiveEnterTimestamp=Thu 2026-03-06 10:30:00 UTC
            line = proc.stdout.strip()
            if "=" in line:
                ts_str = line.split("=", 1)[1].strip()
                if ts_str:
                    # Parse systemctl timestamp format
                    # Try common formats
                    for fmt in [
                        "%a %Y-%m-%d %H:%M:%S %Z",
                        "%a %Y-%m-%d %H:%M:%S %z",
                    ]:
                        try:
                            result["since"] = datetime.strptime(ts_str, fmt)
                            break
                        except ValueError:
                            continue
        except Exception:
            pass  # Non-critical - we still know it's active

    return result


def check_kubeconfig_freshness(config: RKE2Config | None = None) -> dict[str, Any]:
    """Compare mtime of system vs user kubeconfig.

    Returns dict with:
        system_exists: bool
        system_mtime: float | None
        user_exists: bool
        user_mtime: float | None
        stale: bool - True if user copy is older than system
        staleness_seconds: float | None
        readable: bool - whether system kubeconfig is readable
    """
    if config is None:
        config = RKE2Config()

    system_path = Path(config.system_kubeconfig)
    user_path = Path(config.user_kubeconfig).expanduser()

    result: dict[str, Any] = {
        "system_exists": system_path.exists(),
        "system_mtime": None,
        "user_exists": user_path.exists(),
        "user_mtime": None,
        "stale": False,
        "staleness_seconds": None,
        "readable": False,
    }

    # Check system kubeconfig
    if result["system_exists"]:
        try:
            stat = system_path.stat()
            result["system_mtime"] = stat.st_mtime
            result["readable"] = os.access(system_path, os.R_OK)
        except PermissionError:
            result["readable"] = False

    # Check user kubeconfig
    if result["user_exists"]:
        try:
            stat = user_path.stat()
            result["user_mtime"] = stat.st_mtime
        except (PermissionError, OSError):
            pass

    # Determine staleness
    if result["system_mtime"] is not None and result["user_mtime"] is not None:
        # Primary strategy: compare mtimes
        if result["system_mtime"] > result["user_mtime"]:
            result["stale"] = True
            result["staleness_seconds"] = result["system_mtime"] - result["user_mtime"]
    elif result["system_exists"] and not result["readable"] and result["user_mtime"] is not None:
        # Fallback: use service start time when system kubeconfig unreadable
        service = detect_rke2_service(config)
        if service.get("since"):
            service_ts = service["since"].timestamp()
            if service_ts > result["user_mtime"]:
                result["stale"] = True
                result["staleness_seconds"] = service_ts - result["user_mtime"]

    return result


def _test_kubectl(config: RKE2Config | None = None) -> bool:
    """Test kubectl connectivity using the user kubeconfig."""
    if config is None:
        config = RKE2Config()

    user_path = Path(config.user_kubeconfig).expanduser()
    if not user_path.exists():
        return False

    try:
        proc = subprocess.run(
            ["kubectl", "--kubeconfig", str(user_path), "cluster-info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


def get_rke2_status(config: RKE2Config | None = None) -> RKE2Status:
    """Get full RKE2 environment status.

    Combines service detection, kubeconfig freshness, and kubectl test.
    """
    if config is None:
        config = RKE2Config()

    service = detect_rke2_service(config)
    freshness = check_kubeconfig_freshness(config)
    kubectl_ok = _test_kubectl(config)

    user_path = Path(config.user_kubeconfig).expanduser()
    user = getpass.getuser()
    refresh_cmd = (
        f"sudo cp {config.system_kubeconfig} {user_path} && "
        f"sudo chown {user}:{user} {user_path} && "
        f"sudo chmod 600 {user_path}"
    )

    needs_refresh = freshness["stale"] or (
        freshness["system_exists"] and not freshness["user_exists"]
    )

    return RKE2Status(
        service_active=service["active"],
        service_since=service.get("since"),
        system_kubeconfig_exists=freshness["system_exists"],
        system_kubeconfig_mtime=freshness["system_mtime"],
        user_kubeconfig_exists=freshness["user_exists"],
        user_kubeconfig_mtime=freshness["user_mtime"],
        kubeconfig_stale=freshness["stale"],
        staleness_seconds=freshness.get("staleness_seconds"),
        kubectl_connected=kubectl_ok,
        needs_refresh=needs_refresh,
        refresh_command=refresh_cmd,
    )


def refresh_kubeconfig(
    config: RKE2Config | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Refresh user kubeconfig from system copy.

    Copies /etc/rancher/rke2/rke2.yaml to ~/.kube/rke2.yaml with
    correct ownership and permissions.

    Args:
        config: RKE2 configuration
        dry_run: If True, show what would be done without executing

    Returns:
        Dict with success, message, and details
    """
    if config is None:
        config = RKE2Config()

    system_path = Path(config.system_kubeconfig)
    user_path = Path(config.user_kubeconfig).expanduser()
    user = getpass.getuser()

    result: dict[str, Any] = {
        "action": "refresh_kubeconfig",
        "system_path": str(system_path),
        "user_path": str(user_path),
    }

    if not system_path.exists():
        result["success"] = False
        result["message"] = f"System kubeconfig not found: {system_path}"
        return result

    refresh_cmd = (
        f"sudo cp {system_path} {user_path} && "
        f"sudo chown {user}:{user} {user_path} && "
        f"sudo chmod 600 {user_path}"
    )
    result["command"] = refresh_cmd

    if dry_run:
        result["success"] = True
        result["dry_run"] = True
        result["message"] = f"Would refresh kubeconfig: {refresh_cmd}"
        return result

    # Ensure target directory exists
    user_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            ["sudo", "cp", str(system_path), str(user_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            result["success"] = False
            result["message"] = f"sudo cp failed: {proc.stderr.strip()}"
            return result

        proc = subprocess.run(
            ["sudo", "chown", f"{user}:{user}", str(user_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            result["success"] = False
            result["message"] = f"sudo chown failed: {proc.stderr.strip()}"
            return result

        proc = subprocess.run(
            ["sudo", "chmod", "600", str(user_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            result["success"] = False
            result["message"] = f"sudo chmod failed: {proc.stderr.strip()}"
            return result

        # Verify kubectl works with refreshed kubeconfig
        kubectl_ok = _test_kubectl(config)

        result["success"] = True
        result["kubectl_connected"] = kubectl_ok
        if kubectl_ok:
            result["message"] = "Kubeconfig refreshed successfully, kubectl connected"
        else:
            result["message"] = "Kubeconfig refreshed but kubectl not connecting (cluster may be down)"

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["message"] = "sudo command timed out"
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error refreshing kubeconfig: {e}"

    return result
