"""Runtime configuration gathering.

Collects actual runtime state (service health, paths existence, etc.)
and merges with static HOCON config for complete validation.
"""

import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx


async def gather_runtime_config() -> dict[str, Any]:
    """Gather runtime configuration and system state.

    This collects actual values from the running system:
    - Platform detection
    - Path existence checks
    - Service health probes
    - Process state (Flink TaskManager, etc.)
    - Kubernetes target detection

    Returns:
        Runtime configuration dict
    """
    runtime: dict[str, Any] = {
        "platform": {},
        "paths": {},
        "python": {},
        "services": {},
        "flink": {},
        "kubernetes": {},
    }

    # Platform detection
    is_macos = platform.system() == "Darwin"
    is_linux = platform.system() == "Linux"
    runtime["platform"] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "is_macos": is_macos,
        "is_linux": is_linux,
    }

    # Path checks
    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    flink_home = os.environ.get(
        "FLINK_HOME",
        f"{devenv_root}/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
    )

    runtime["paths"] = {
        "root": devenv_root,
        "root_exists": Path(devenv_root).exists(),
        "flink_home": flink_home,
        "flink_home_exists": Path(flink_home).exists(),
        "flink_home_env_set": bool(os.environ.get("FLINK_HOME")),
        "flink_binary_exists": Path(flink_home, "bin", "flink").exists(),
        "flink_conf_exists": Path(flink_home, "conf", "config.yaml").exists(),
    }

    # Python environment
    pyflink_installed = False
    pyflink_version = None
    try:
        import pyflink
        pyflink_installed = True
        pyflink_version = getattr(pyflink, "__version__", "unknown")
    except ImportError:
        pass

    devenv_python = f"{devenv_root}/.devenv/profile/bin/python3"

    runtime["python"] = {
        "version": sys.version.split()[0],
        "executable": sys.executable,
        "pyflink_installed": pyflink_installed,
        "pyflink_version": pyflink_version,
        "devenv_python": devenv_python,
        "devenv_python_exists": Path(devenv_python).exists(),
    }

    # Flink configuration state (Flink 1.20+ uses config.yaml)
    flink_conf_path = Path(flink_home, "conf", "config.yaml")
    python_configured = False
    configured_python_path = ""
    configured_python_exists = False
    config_mtime = None

    if flink_conf_path.exists():
        try:
            import yaml

            config_mtime = os.path.getmtime(flink_conf_path)
            content = flink_conf_path.read_text()
            config = yaml.safe_load(content) or {}
            python_config = config.get("python", {})

            # Check python.executable
            if python_config.get("executable"):
                python_configured = True
                configured_python_path = python_config["executable"]
                configured_python_exists = Path(configured_python_path).exists()
            # Fall back to python.client.executable
            elif python_config.get("client", {}).get("executable"):
                python_configured = True
                configured_python_path = python_config["client"]["executable"]
                configured_python_exists = Path(configured_python_path).exists()
        except Exception:
            pass

    # Check for stale Flink process
    process_stale = False
    taskmanager_running = False
    try:
        result = subprocess.run(
            ["pgrep", "-f", "TaskManager"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            taskmanager_running = True
            tm_pid = result.stdout.strip().split()[0]

            # On Linux, check process start time vs config mtime
            proc_stat = Path(f"/proc/{tm_pid}/stat")
            if proc_stat.exists() and config_mtime:
                try:
                    boot_time = None
                    with open("/proc/stat") as f:
                        for line in f:
                            if line.startswith("btime"):
                                boot_time = int(line.split()[1])
                                break
                    if boot_time:
                        with open(proc_stat) as f:
                            stat_fields = f.read().split()
                            starttime_ticks = int(stat_fields[21])
                            clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                            tm_start = boot_time + (starttime_ticks / clk_tck)
                            if config_mtime > tm_start:
                                process_stale = True
                except Exception:
                    pass
    except Exception:
        pass

    runtime["flink"] = {
        "python_configured": python_configured,
        "configured_python_path": configured_python_path,
        "configured_python_exists": configured_python_exists,
        "config_mtime": config_mtime,
        "taskmanager_running": taskmanager_running,
        "process_stale": process_stale,
    }

    # Service health checks
    runtime["services"] = {
        "postgres": await _check_tcp("localhost", 5438),
        "minio": await _check_http("http://localhost:9010/minio/health/live"),
        "polaris": await _check_http("http://localhost:8182/q/health/ready"),
        "flink": await _check_flink("http://localhost:8081"),
        "iceberg_browser": await _check_http("http://localhost:5050/health"),
        "prometheus": await _check_http("http://localhost:9090/-/ready"),
        "otel_collector": await _check_tcp("localhost", 4317),
    }

    # Kubernetes target detection
    runtime["kubernetes"] = _detect_kubernetes_target()

    return runtime


def _detect_kubernetes_target() -> dict[str, Any]:
    """Detect Kubernetes target type from environment and kubeconfig.

    Returns:
        Dictionary with kubernetes configuration state:
        - enabled: Whether ENABLE_K8S is set (master switch)
        - target: "none", "k3d", or "rke2"
        - needs_k3d_provisioning: Whether k3d cluster needs to be created
        - kubeconfig_exists: Whether KUBECONFIG file exists
        - cluster_type: Detected cluster type from kubeconfig
        - kubectl_available: Whether kubectl is on PATH
        - kubectl_connected: Whether kubectl can reach the cluster
    """
    result: dict[str, Any] = {
        "enabled": False,
        "target": "none",
        "needs_k3d_provisioning": False,
        "kubeconfig_exists": False,
        "kubeconfig_path": "",
        "cluster_type": "none",
        "kubectl_available": False,
        "kubectl_connected": False,
    }

    # Check ENABLE_K8S environment variable (master switch)
    enable_k8s = os.environ.get("ENABLE_K8S", "false").lower()
    result["enabled"] = enable_k8s == "true"

    # Check CYBERSEC_K8S_TARGET (set by enterShell or explicitly)
    k8s_target = os.environ.get("CYBERSEC_K8S_TARGET", "auto")
    if k8s_target in ("k3d", "rke2"):
        result["target"] = k8s_target

    # Check kubeconfig
    kubeconfig_path = os.environ.get("KUBECONFIG", "")
    if kubeconfig_path:
        result["kubeconfig_path"] = kubeconfig_path
        kubeconfig_file = Path(kubeconfig_path)
        if kubeconfig_file.exists():
            result["kubeconfig_exists"] = True
            try:
                content = kubeconfig_file.read_text()
                if "rancher" in content or "rke2" in content:
                    result["cluster_type"] = "rke2"
                    if result["target"] in ("none", "auto"):
                        result["target"] = "rke2"
                elif "k3d" in content or "k3s" in content:
                    result["cluster_type"] = "k3d"
                    if result["target"] in ("none", "auto"):
                        result["target"] = "k3d"
            except Exception:
                pass

    # Check kubectl availability
    try:
        subprocess.run(
            ["kubectl", "version", "--client", "--short"],
            capture_output=True,
            timeout=5,
        )
        result["kubectl_available"] = True

        # Check cluster connectivity if kubeconfig exists
        if result["kubeconfig_exists"]:
            try:
                conn_result = subprocess.run(
                    ["kubectl", "get", "nodes", "-o", "name"],
                    capture_output=True,
                    timeout=10,
                    env={**os.environ, "KUBECONFIG": kubeconfig_path},
                )
                result["kubectl_connected"] = conn_result.returncode == 0
            except Exception:
                pass
    except Exception:
        pass

    # When ENABLE_K8S is set but no target detected, default to k3d
    if result["enabled"] and result["target"] in ("none", "auto"):
        result["target"] = "k3d"

    # Determine if k3d provisioning is needed
    # k3d provisioning runs when: enabled + target=k3d + no existing kubeconfig
    result["needs_k3d_provisioning"] = (
        result["enabled"]
        and result["target"] == "k3d"
        and not result["kubeconfig_exists"]
    )

    return result


async def _check_tcp(host: str, port: int) -> dict[str, Any]:
    """Check TCP connectivity."""
    result = {"host": host, "port": port, "healthy": False}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((host, port))
        sock.close()
        result["healthy"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


async def _check_http(url: str) -> dict[str, Any]:
    """Check HTTP endpoint health."""
    result = {"url": url, "healthy": False}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            result["healthy"] = resp.status_code == 200
            result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


async def _check_flink(url: str) -> dict[str, Any]:
    """Check Flink cluster health."""
    result = {
        "url": url,
        "jobmanager_healthy": False,
        "taskmanager_count": 0,
        "slots_total": 0,
        "slots_available": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/overview")
            if resp.status_code == 200:
                result["jobmanager_healthy"] = True
                data = resp.json()
                result["taskmanager_count"] = data.get("taskmanagers", 0)
                result["slots_total"] = data.get("slots-total", 0)
                result["slots_available"] = data.get("slots-available", 0)
    except Exception as e:
        result["error"] = str(e)
    return result


def merge_runtime_config(
    static_config: dict[str, Any],
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    """Merge static HOCON config with runtime state.

    Creates a unified config suitable for policy validation that includes:
    - Static configuration values (from HOCON)
    - Runtime state (service health, path existence, etc.)

    Args:
        static_config: Hydrated HOCON configuration
        runtime_config: Runtime state from gather_runtime_config()

    Returns:
        Merged configuration dict
    """
    merged = {
        "static": static_config,
        "runtime": runtime_config,
    }

    # Create a flat "effective" view for policy validation
    # This combines static config with runtime checks
    cybersec = static_config.get("cybersec", {})
    services_config = cybersec.get("services", {})
    paths_config = cybersec.get("paths", {})

    merged["effective"] = {
        "platform": runtime_config.get("platform", {}),

        "paths": {
            **paths_config,
            **runtime_config.get("paths", {}),
        },

        "python": {
            **cybersec.get("python", {}),
            **runtime_config.get("python", {}),
        },

        "flink": {
            **services_config.get("flink", {}),
            **runtime_config.get("flink", {}),
            "home": runtime_config.get("paths", {}).get("flink_home", ""),
            "home_exists": runtime_config.get("paths", {}).get("flink_home_exists", False),
            "home_env_set": runtime_config.get("paths", {}).get("flink_home_env_set", False),
            "binary_exists": runtime_config.get("paths", {}).get("flink_binary_exists", False),
        },

        "services": {
            "postgres": {
                **services_config.get("postgres", {}),
                **runtime_config.get("services", {}).get("postgres", {}),
            },
            "minio": {
                **services_config.get("minio", {}),
                **runtime_config.get("services", {}).get("minio", {}),
            },
            "polaris": {
                **services_config.get("polaris", {}),
                **runtime_config.get("services", {}).get("polaris", {}),
            },
            "flink": {
                **services_config.get("flink", {}),
                **runtime_config.get("services", {}).get("flink", {}),
            },
            "iceberg_browser": {
                **services_config.get("iceberg_browser", {}),
                **runtime_config.get("services", {}).get("iceberg_browser", {}),
            },
        },

        "kubernetes": {
            **cybersec.get("kubernetes", {}),
            **runtime_config.get("kubernetes", {}),
        },
    }

    return merged
