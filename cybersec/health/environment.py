"""Environment configuration generator for conftest policies.

Generates a JSON snapshot of the current environment state that can be
validated against Rego policies using conftest.
"""

import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx


async def gather_environment_config() -> dict[str, Any]:
    """Gather complete environment configuration for policy validation.

    Returns:
        Environment config dict suitable for conftest validation.
    """
    from ..bootstrap import BootstrapService
    from ..config.runtime import _detect_kubernetes_target

    config: dict[str, Any] = {
        "platform": {},
        "python": {},
        "flink": {},
        "services": {},
        "kubernetes": {},
    }

    # Platform info
    is_macos = platform.system() == "Darwin"
    config["platform"] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "is_macos": is_macos,
        "is_linux": platform.system() == "Linux",
    }

    # Python environment
    pyflink_installed = False
    try:
        import pyflink
        pyflink_installed = True
        config["python"]["pyflink_version"] = getattr(pyflink, "__version__", "unknown")
    except ImportError:
        pass

    kafka_installed = False
    try:
        import kafka
        kafka_installed = True
    except ImportError:
        pass

    config["python"] = {
        "version": sys.version.split()[0],
        "executable": sys.executable,
        "pyflink_installed": pyflink_installed,
        "kafka_installed": kafka_installed,
    }

    # Check devenv python
    devenv_root = os.environ.get("DEVENV_ROOT", "")
    if devenv_root:
        devenv_python = f"{devenv_root}/.devenv/profile/bin/python3"
        config["python"]["devenv_python"] = devenv_python
        config["python"]["devenv_python_exists"] = Path(devenv_python).exists()

    # Flink configuration
    service = BootstrapService()
    bootstrap_config = service.get_config()
    flink_home = bootstrap_config.get_flink_home()

    flink_home_exists = flink_home.exists() if flink_home else False
    flink_home_env = os.environ.get("FLINK_HOME", "")
    flink_home_env_set = bool(flink_home_env)

    config["flink"] = {
        "home": str(flink_home) if flink_home else "",
        "home_exists": flink_home_exists,
        "home_env": flink_home_env,
        "home_env_set": flink_home_env_set,
        "binary_exists": False,
        "python_configured": False,
        "configured_python_path": "",
        "configured_python_exists": False,
        "process_stale": False,
    }

    if flink_home and flink_home_exists:
        flink_bin = flink_home / "bin" / "flink"
        config["flink"]["binary_exists"] = flink_bin.exists()

        flink_conf = flink_home / "conf" / "flink-conf.yaml"
        if flink_conf.exists():
            try:
                content = flink_conf.read_text()
                config_mtime = os.path.getmtime(flink_conf)
                config["flink"]["config_mtime"] = config_mtime

                python_settings = [
                    line.strip() for line in content.split("\n")
                    if ("python.executable" in line.lower() or "python.client.executable" in line.lower())
                    and not line.strip().startswith("#")
                ]
                config["flink"]["python_configured"] = len(python_settings) > 0
                config["flink"]["python_settings"] = python_settings

                # Extract configured path
                for setting in python_settings:
                    if "python.executable:" in setting and "client" not in setting:
                        configured_path = setting.split(":", 1)[1].strip()
                        config["flink"]["configured_python_path"] = configured_path
                        config["flink"]["configured_python_exists"] = Path(configured_path).exists()
                        break
                    elif "python.client.executable:" in setting:
                        configured_path = setting.split(":", 1)[1].strip()
                        if not config["flink"]["configured_python_path"]:
                            config["flink"]["configured_python_path"] = configured_path
                            config["flink"]["configured_python_exists"] = Path(configured_path).exists()

                # Check if process is stale
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", "TaskManager"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        tm_pid = result.stdout.strip().split()[0]
                        # On Linux, check /proc for process start time
                        proc_stat = Path(f"/proc/{tm_pid}/stat")
                        if proc_stat.exists():
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
                                        config["flink"]["process_stale"] = True
                except Exception:
                    pass

            except Exception as e:
                config["flink"]["config_error"] = str(e)

    # Services health checks
    config["services"] = {
        "postgres": await _check_postgres(bootstrap_config.postgres_port or 5438),
        "minio": await _check_minio(bootstrap_config.minio_endpoint or "http://localhost:9010"),
        "polaris": await _check_polaris(bootstrap_config.polaris_api_url or "http://localhost:8181"),
        "flink": await _check_flink(bootstrap_config.flink_url or "http://localhost:8081"),
        "iceberg_browser": await _check_http(
            "http://localhost:5050/health",
            bootstrap_config.iceberg_browser_port or 5050
        ),
        # Additional observability services
        "nifi": await _check_http("http://localhost:8450/nifi-api/system-diagnostics", 8450),
        "otel_collector": await _check_otel_collector(),
        "prometheus": await _check_http("http://localhost:9090/-/healthy", 9090),
    }

    # Kubernetes configuration
    config["kubernetes"] = _detect_kubernetes_target()

    # ngrok and Cloudflare credentials (required for AWS deployments)
    config["services"]["ngrok"] = _check_ngrok_credentials()
    config["services"]["cloudflare"] = _check_cloudflare_credentials()

    # AWS credentials and IAM permissions
    config["aws"] = _check_aws_credentials()

    # Deployment tools availability
    config["tools"] = _check_deployment_tools()

    # Python packages
    config["python"]["packages"] = _check_python_packages()

    return config


async def _check_postgres(port: int) -> dict[str, Any]:
    """Check PostgreSQL connectivity."""
    result = {"port": port, "healthy": False}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(("localhost", port))
        sock.close()
        result["healthy"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


async def _check_minio(endpoint: str) -> dict[str, Any]:
    """Check MinIO health."""
    result = {"endpoint": endpoint, "healthy": False}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{endpoint}/minio/health/live")
            result["healthy"] = resp.status_code == 200
            result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


async def _check_polaris(url: str) -> dict[str, Any]:
    """Check Polaris health."""
    result = {"url": url, "healthy": False}
    try:
        # Polaris admin health endpoint
        admin_url = url.replace(":8181", ":8182")
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{admin_url}/q/health/ready")
            result["healthy"] = resp.status_code == 200
            result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


async def _check_flink(url: str) -> dict[str, Any]:
    """Check Flink JobManager and TaskManagers."""
    result = {"url": url, "jobmanager_healthy": False, "taskmanager_count": 0}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Check overview
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


async def _check_http(url: str, port: int) -> dict[str, Any]:
    """Generic HTTP health check."""
    result = {"url": url, "port": port, "healthy": False}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            result["healthy"] = resp.status_code == 200
            result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


def _check_ngrok_credentials() -> dict[str, Any]:
    """Check ngrok credential availability.

    ngrok credentials are required for AWS deployments to expose
    services externally via tunnels.
    """
    auth_token = os.environ.get("NGROK_AUTH_TOKEN") or os.environ.get("NGROK_AUTHTOKEN", "")
    api_key = os.environ.get("NGROK_API_KEY", "")

    return {
        # Only expose boolean flags, not actual credentials
        "auth_token_set": bool(auth_token),
        "api_key_set": bool(api_key),
        "credentials_complete": bool(auth_token) and bool(api_key),
        "domains": {
            "dask": os.environ.get("NGROK_DASK_DOMAIN", "dask.zndx.org"),
            "jupyterhub": os.environ.get("NGROK_JUPYTERHUB_DOMAIN", "jupyter.zndx.org"),
            "k8s_dashboard": os.environ.get("NGROK_K8S_DOMAIN", "k8s.zndx.org"),
        },
    }


def _check_cloudflare_credentials() -> dict[str, Any]:
    """Check Cloudflare credential availability.

    Cloudflare credentials are required for custom domain DNS management
    when using ngrok with custom domains.
    """
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    zone_id = os.environ.get("CLOUDFLARE_ZONE_ID", "")

    return {
        # Only expose boolean flags, not actual credentials
        "api_token_set": bool(api_token),
        "zone_id_set": bool(zone_id),
        "credentials_complete": bool(api_token),  # zone_id optional for some operations
    }


def _check_aws_credentials() -> dict[str, Any]:
    """Check AWS credentials and IAM permissions.

    Validates that AWS credentials are configured and have the required
    permissions for AWS deployments (S3, EC2, IAM operations).
    """
    import json
    import subprocess

    result = {
        "credentials_configured": False,
        "caller_identity": None,
        "account_id": None,
        "arn": None,
        "permissions": {
            "s3_access": False,
            "ec2_describe": False,
        },
        "error": None,
    }

    # Check if AWS CLI is available
    try:
        # Get caller identity to verify credentials
        identity_proc = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if identity_proc.returncode == 0:
            identity = json.loads(identity_proc.stdout)
            result["credentials_configured"] = True
            result["account_id"] = identity.get("Account")
            result["arn"] = identity.get("Arn")
            result["caller_identity"] = identity.get("UserId")

            # Check S3 access by listing buckets (minimal permission test)
            s3_proc = subprocess.run(
                ["aws", "s3", "ls", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            result["permissions"]["s3_access"] = s3_proc.returncode == 0

            # Check EC2 describe (for deployment verification)
            ec2_proc = subprocess.run(
                ["aws", "ec2", "describe-regions", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            result["permissions"]["ec2_describe"] = ec2_proc.returncode == 0

        else:
            result["error"] = identity_proc.stderr.strip() or "AWS credentials not configured"

    except FileNotFoundError:
        result["error"] = "AWS CLI not installed"
    except subprocess.TimeoutExpired:
        result["error"] = "AWS CLI timeout - check network connectivity"
    except json.JSONDecodeError as e:
        result["error"] = f"Failed to parse AWS response: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


async def _check_otel_collector() -> dict[str, Any]:
    """Check OpenTelemetry Collector health."""
    result = {"healthy": False, "grpc_port": 4317, "http_port": 4318, "prometheus_port": 8889}
    try:
        # Check the prometheus metrics endpoint (most reliable)
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:8889/metrics")
            result["healthy"] = resp.status_code == 200
    except Exception as e:
        result["error"] = str(e)
    return result


def _check_deployment_tools() -> dict[str, Any]:
    """Check availability of deployment tools (CLI binaries)."""
    import shutil

    tools = {
        # Local K8s tools
        "kubectl": shutil.which("kubectl") is not None,
        "helm": shutil.which("helm") is not None,
        "k3d": shutil.which("k3d") is not None,
        # AWS deployment tools
        "tofu": shutil.which("tofu") is not None,
        "terraform": shutil.which("terraform") is not None,
        "ansible": shutil.which("ansible") is not None,
        "ansible_playbook": shutil.which("ansible-playbook") is not None,
        "aws_cli": shutil.which("aws") is not None,
        # Validation tools
        "conftest": shutil.which("conftest") is not None,
    }

    # Check for SSH key (required for AWS deployments)
    ssh_key_paths = [
        Path.home() / ".ssh" / "cybersec-dask.pem",
        Path.home() / ".ssh" / "id_rsa",
        Path.home() / ".ssh" / "id_ed25519",
    ]
    tools["ssh_key_exists"] = any(p.exists() for p in ssh_key_paths)
    tools["ssh_key_path"] = next((str(p) for p in ssh_key_paths if p.exists()), None)

    # Check for Ansible inventory (indicates AWS deployment is configured)
    ansible_inventory = Path("infra/aws/ansible/inventory/hosts")
    tools["ansible_inventory_exists"] = ansible_inventory.exists()

    # Check for Tofu state (indicates infrastructure exists)
    tofu_state = Path("infra/aws/tofu/terraform.tfstate")
    tools["tofu_state_exists"] = tofu_state.exists()

    # Infrastructure-as-code tool (prefer tofu over terraform)
    tools["iac_tool"] = "tofu" if tools["tofu"] else ("terraform" if tools["terraform"] else None)

    return tools


def _check_python_packages() -> dict[str, Any]:
    """Check required Python packages are installed."""
    packages = {
        "apache_flink": False,
        "pyiceberg": False,
        "httpx": False,
        "dask": False,
        "distributed": False,
        "kubernetes": False,
    }

    # Check each package
    try:
        import pyflink
        packages["apache_flink"] = True
    except ImportError:
        pass

    try:
        import pyiceberg
        packages["pyiceberg"] = True
    except ImportError:
        pass

    try:
        import httpx
        packages["httpx"] = True
    except ImportError:
        pass

    try:
        import dask
        packages["dask"] = True
    except ImportError:
        pass

    try:
        import distributed
        packages["distributed"] = True
    except ImportError:
        pass

    try:
        import kubernetes
        packages["kubernetes"] = True
    except ImportError:
        pass

    # Core packages required for local Flink stack
    packages["flink_stack_complete"] = all([
        packages["apache_flink"],
        packages["pyiceberg"],
        packages["httpx"],
    ])

    # Packages required for Dask integration
    packages["dask_stack_complete"] = all([
        packages["dask"],
        packages["distributed"],
    ])

    return packages


async def write_environment_config(output_path: Path | None = None) -> Path:
    """Gather environment config and write to JSON file.

    Args:
        output_path: Output file path. Defaults to build/environment.json

    Returns:
        Path to the written config file
    """
    if output_path is None:
        output_path = Path("build/environment.json")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    config = await gather_environment_config()

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    return output_path


def run_conftest(config_path: Path, policy_dir: Path | None = None) -> dict[str, Any]:
    """Run conftest against the environment config.

    Args:
        config_path: Path to environment.json
        policy_dir: Policy directory. Defaults to policy/environment/

    Returns:
        Conftest results with failures, warnings, and successes
    """
    if policy_dir is None:
        policy_dir = Path("policy/environment")

    result = {
        "success": True,
        "failures": [],
        "warnings": [],
        "output": "",
    }

    try:
        proc = subprocess.run(
            [
                "conftest", "test",
                str(config_path),
                "--policy", str(policy_dir),
                "--all-namespaces",
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        result["output"] = proc.stdout
        result["stderr"] = proc.stderr

        if proc.stdout:
            try:
                conftest_output = json.loads(proc.stdout)
                for item in conftest_output:
                    for failure in item.get("failures", []):
                        result["failures"].append(failure.get("msg", str(failure)))
                    for warning in item.get("warnings", []):
                        result["warnings"].append(warning.get("msg", str(warning)))

                result["success"] = len(result["failures"]) == 0
            except json.JSONDecodeError:
                result["parse_error"] = "Failed to parse conftest JSON output"

        # Non-zero exit with failures is expected
        if proc.returncode != 0 and not result["failures"]:
            result["success"] = False
            result["error"] = proc.stderr or f"conftest exited with code {proc.returncode}"

    except FileNotFoundError:
        result["success"] = False
        result["error"] = "conftest not found. Install with: nix-env -iA nixpkgs.conftest"
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["error"] = "conftest timed out"
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)

    return result
