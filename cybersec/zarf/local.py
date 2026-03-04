"""Local Zarf deployment configuration gathering.

Collects runtime state for local Zarf deployment validation:
- Node resources (memory, disk, storage classes)
- Zarf-specific local config (packages, spill dir, replicas)
- Extends base runtime config for conftest policy consumption
"""

import glob
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from ..config.runtime import gather_runtime_config


async def gather_local_zarf_config() -> dict[str, Any]:
    """Gather complete config for local Zarf deployment policy validation.

    Extends the base runtime config with node_resources and zarf_local
    sections needed by policy/k8s/local/requirements.rego.

    Returns:
        Flat dict for conftest: {platform, tools, kubernetes, services,
        node_resources, zarf_local}
    """
    runtime = await gather_runtime_config()

    # Extend with local-specific sections
    runtime["node_resources"] = await _gather_node_resources(runtime)
    runtime["zarf_local"] = _gather_zarf_local_config(runtime)

    # Gather registry state (only when connected to cluster)
    k8s = runtime.get("kubernetes", {})
    if k8s.get("kubectl_connected", False):
        runtime["zarf_local"]["zarf_registry"] = _gather_zarf_registry_state()
    else:
        runtime["zarf_local"]["zarf_registry"] = _empty_registry_state()

    # Return flat dict for policy consumption
    return {
        "platform": runtime.get("platform", {}),
        "tools": runtime.get("tools", {}),
        "kubernetes": runtime.get("kubernetes", {}),
        "services": runtime.get("services", {}),
        "node_resources": runtime["node_resources"],
        "zarf_local": runtime["zarf_local"],
    }


async def _gather_node_resources(runtime: dict[str, Any]) -> dict[str, Any]:
    """Gather node resource information from kubectl or local system.

    Tries kubectl first (gets actual cluster node capacity), falls back
    to local system info.

    Args:
        runtime: Base runtime config (for kubectl_connected check)

    Returns:
        Dict with total_memory_gb, disk_free_pct, disk_pressure,
        storage_classes, default_storage_class, ingress_classes
    """
    result: dict[str, Any] = {
        "total_memory_gb": 0,
        "disk_free_pct": 0,
        "disk_pressure": False,
        "storage_classes": [],
        "default_storage_class": "",
        "ingress_classes": [],
    }

    k8s = runtime.get("kubernetes", {})
    connected = k8s.get("kubectl_connected", False)

    # Memory: try kubectl node capacity, fallback to local
    if connected:
        try:
            proc = subprocess.run(
                ["kubectl", "get", "nodes", "-o", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                import json
                data = json.loads(proc.stdout)
                nodes = data.get("items", [])
                if nodes:
                    node = nodes[0]
                    capacity = node.get("status", {}).get("capacity", {})
                    mem_str = capacity.get("memory", "")
                    if mem_str:
                        result["total_memory_gb"] = _parse_k8s_memory_to_gb(mem_str)

                    # Check DiskPressure condition/taints
                    conditions = node.get("status", {}).get("conditions", [])
                    for cond in conditions:
                        if cond.get("type") == "DiskPressure" and cond.get("status") == "True":
                            result["disk_pressure"] = True
                            break

                    taints = node.get("spec", {}).get("taints", [])
                    for taint in taints:
                        if taint.get("key") == "node.kubernetes.io/disk-pressure":
                            result["disk_pressure"] = True
                            break
        except Exception:
            pass

    # Fallback to local memory if kubectl didn't provide it
    if result["total_memory_gb"] == 0:
        result["total_memory_gb"] = _get_local_memory_gb()

    # Disk free percentage (always local — relevant for single-node)
    result["disk_free_pct"] = _get_local_disk_free_pct()

    # Storage classes from cluster
    if connected:
        try:
            proc = subprocess.run(
                ["kubectl", "get", "storageclass", "-o", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                import json
                data = json.loads(proc.stdout)
                for sc in data.get("items", []):
                    name = sc.get("metadata", {}).get("name", "")
                    if name:
                        result["storage_classes"].append(name)
                    annotations = sc.get("metadata", {}).get("annotations", {})
                    if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                        result["default_storage_class"] = name
        except Exception:
            pass

    # Ingress classes from cluster
    if connected:
        try:
            proc = subprocess.run(
                ["kubectl", "get", "ingressclass", "-o", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                import json
                data = json.loads(proc.stdout)
                for ic in data.get("items", []):
                    name = ic.get("metadata", {}).get("name", "")
                    if name:
                        result["ingress_classes"].append(name)
        except Exception:
            pass

    return result


def _gather_zarf_local_config(_runtime: dict[str, Any]) -> dict[str, Any]:
    """Gather Zarf-specific local deployment configuration.

    Args:
        _runtime: Base runtime config (reserved for future use)

    Returns:
        Dict with init/deploy package status, spill dir, worker replicas, etc.
    """
    project_root = Path(__file__).parent.parent.parent
    zarf_dir = project_root / "zarf"

    spill_dir = os.environ.get("DASK_SPILL_DIR", "")

    return {
        "init_package_exists": bool(
            glob.glob(str(zarf_dir / "zarf-init-*.tar.zst"))
        ),
        "deploy_package_exists": bool(
            glob.glob(str(zarf_dir / "zarf-package-cybersec-dask-*.tar.zst"))
        ),
        "spill_dir": spill_dir,
        "spill_dir_exists": Path(spill_dir).is_dir() if spill_dir else False,
        "worker_replicas": int(os.environ.get("DASK_WORKER_REPLICAS", "2")),
        "registry_pvc_enabled": False,
        "minio_endpoint": os.environ.get(
            "S3_ENDPOINT",
            f"http://localhost:{os.environ.get('LOCAL_S3_PORT', '9010')}",
        ),
        "minio_bucket": "cybersec",
    }


def _empty_registry_state() -> dict[str, Any]:
    """Return empty registry state when cluster is not connected."""
    return {
        "namespace_exists": False,
        "pvc_exists": False,
        "pvc_phase": "",
        "pvc_volume_name": "",
        "pv_exists": False,
        "pv_phase": "",
        "pv_has_claim_ref": False,
        "has_stuck_finalizers": False,
    }


def _gather_zarf_registry_state() -> dict[str, Any]:
    """Gather Zarf registry PVC/PV state from the cluster.

    Uses the same subprocess/kubectl pattern as _gather_node_resources().
    Detects stale state from failed `zarf init` attempts: Lost PVCs,
    missing claimRef on PVs, stuck finalizers.

    Returns:
        Dict with namespace_exists, pvc_exists, pvc_phase, pvc_volume_name,
        pv_exists, pv_phase, pv_has_claim_ref, has_stuck_finalizers
    """
    import json

    result = _empty_registry_state()

    # Check namespace existence
    try:
        proc = subprocess.run(
            ["kubectl", "get", "namespace", "zarf", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            result["namespace_exists"] = True
    except Exception:
        pass

    # Check PVC state
    try:
        proc = subprocess.run(
            ["kubectl", "get", "pvc", "zarf-docker-registry",
             "-n", "zarf", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            result["pvc_exists"] = True
            result["pvc_phase"] = data.get("status", {}).get("phase", "")
            result["pvc_volume_name"] = (
                data.get("spec", {}).get("volumeName", "")
            )
            finalizers = data.get("metadata", {}).get("finalizers", [])
            if finalizers:
                result["has_stuck_finalizers"] = True
    except Exception:
        pass

    # Check PV state
    try:
        proc = subprocess.run(
            ["kubectl", "get", "pv", "zarf-registry-pv", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            result["pv_exists"] = True
            result["pv_phase"] = data.get("status", {}).get("phase", "")
            claim_ref = data.get("spec", {}).get("claimRef", {})
            if claim_ref.get("name"):
                result["pv_has_claim_ref"] = True
            pv_finalizers = data.get("metadata", {}).get("finalizers", [])
            if pv_finalizers:
                result["has_stuck_finalizers"] = True
    except Exception:
        pass

    return result


def _parse_k8s_memory_to_gb(mem_str: str) -> int:
    """Parse Kubernetes memory string to GB.

    Examples:
        "32841520Ki" → 31
        "16Gi"       → 16
        "8192Mi"     → 8

    Args:
        mem_str: Kubernetes memory quantity string

    Returns:
        Memory in GB (integer, rounded down)
    """
    try:
        if mem_str.endswith("Ki"):
            return int(mem_str[:-2]) // (1024 * 1024)
        elif mem_str.endswith("Mi"):
            return int(mem_str[:-2]) // 1024
        elif mem_str.endswith("Gi"):
            return int(mem_str[:-2])
        elif mem_str.endswith("Ti"):
            return int(mem_str[:-2]) * 1024
        else:
            # Plain bytes
            return int(mem_str) // (1024 * 1024 * 1024)
    except (ValueError, TypeError):
        return 0


def _get_local_memory_gb() -> int:
    """Get local system total memory in GB.

    Uses /proc/meminfo on Linux, sysctl on macOS.

    Returns:
        Total memory in GB (integer)
    """
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # MemTotal is in kB
                        kb = int(line.split()[1])
                        return kb // (1024 * 1024)
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            proc = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                return int(proc.stdout.strip()) // (1024 * 1024 * 1024)
        except Exception:
            pass
    return 0


def _get_local_disk_free_pct() -> int:
    """Get local disk free percentage.

    Checks /var/lib/rancher (RKE2 data dir), then /var, then /.

    Returns:
        Disk free percentage (0-100)
    """
    for path in ["/var/lib/rancher", "/var", "/"]:
        if os.path.exists(path):
            try:
                st = os.statvfs(path)
                if st.f_blocks > 0:
                    return int((st.f_bavail / st.f_blocks) * 100)
            except Exception:
                continue
    return 0
