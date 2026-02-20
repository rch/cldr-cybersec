"""K8s target preparation and validation.

This module handles:
1. Target detection (aws, rke2, k3d)
2. Configuration validation via conftest
3. Target-specific config file generation
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..config.runtime import gather_runtime_config


class K8sTarget(str, Enum):
    """Available K8s deployment targets."""

    AWS = "aws"
    RKE2 = "rke2"
    K3D = "k3d"
    NONE = "none"


@dataclass
class ValidationResult:
    """Result from conftest policy validation."""

    success: bool
    target: K8sTarget
    denies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class PrepareResult:
    """Result from target preparation."""

    success: bool
    target: K8sTarget
    validation: ValidationResult
    config_path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def detect_target() -> K8sTarget:
    """Detect K8s target from environment and kubeconfig.

    Priority:
    1. CYBERSEC_K8S_TARGET environment variable
    2. KUBECONFIG content analysis (rke2/k3d markers)
    3. Default to NONE if no target detected

    Returns:
        Detected K8sTarget
    """
    # Check explicit target setting
    explicit_target = os.environ.get("CYBERSEC_K8S_TARGET", "").lower()
    if explicit_target in ("aws", "rke2", "k3d"):
        return K8sTarget(explicit_target)

    # Check kubeconfig content
    kubeconfig_path = os.environ.get("KUBECONFIG", "")
    if kubeconfig_path:
        kc_file = Path(kubeconfig_path)
        if kc_file.exists():
            try:
                content = kc_file.read_text()
                if "rancher" in content or "rke2" in content:
                    return K8sTarget.RKE2
                elif "k3d" in content or "k3s" in content:
                    return K8sTarget.K3D
            except Exception:
                pass

    # Check for AWS deployment indicators
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("NGROK_AUTH_TOKEN"):
        return K8sTarget.AWS

    return K8sTarget.NONE


async def validate_target(
    target: K8sTarget,
    runtime_config: dict[str, Any] | None = None,
) -> ValidationResult:
    """Validate target requirements using conftest policies.

    Args:
        target: The K8s target to validate
        runtime_config: Optional pre-gathered runtime config

    Returns:
        ValidationResult with policy check results
    """
    if runtime_config is None:
        runtime_config = await gather_runtime_config()

    # Flatten runtime config for policy consumption
    env_config = _flatten_for_policy(runtime_config)

    # Write temporary environment.json for conftest
    build_dir = Path("build")
    build_dir.mkdir(exist_ok=True)
    env_file = build_dir / "environment.json"
    env_file.write_text(json.dumps(env_config, indent=2))

    # Determine policy paths
    project_root = Path(__file__).parent.parent.parent
    base_policy = project_root / "policy" / "k8s" / "base.rego"
    target_policy_dir = project_root / "policy" / "k8s" / target.value

    # Build conftest command
    cmd = [
        "conftest", "test", str(env_file),
        "--policy", str(base_policy),
        "--all-namespaces",
        "--output", "json",
    ]

    # Add target-specific policies if they exist
    if target_policy_dir.exists():
        cmd.extend(["--policy", str(target_policy_dir)])

    # Run conftest
    result = ValidationResult(success=True, target=target)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result.raw_output = proc.stdout + proc.stderr

        if proc.stdout:
            try:
                output = json.loads(proc.stdout)
                for item in output:
                    # conftest returns a list of results
                    if "failures" in item:
                        for failure in item["failures"]:
                            result.denies.append(failure.get("msg", str(failure)))
                    if "warnings" in item:
                        for warning in item["warnings"]:
                            result.warnings.append(warning.get("msg", str(warning)))
                    if "successes" in item:
                        # successes contain info messages in our policies
                        pass
            except json.JSONDecodeError:
                # Parse text output for messages
                for line in proc.stdout.split("\n"):
                    if "FAIL" in line:
                        result.denies.append(line)
                    elif "WARN" in line:
                        result.warnings.append(line)

        # Success if no denies
        result.success = len(result.denies) == 0

    except FileNotFoundError:
        result.success = False
        result.denies.append("conftest not installed. Install: brew install conftest")
    except subprocess.TimeoutExpired:
        result.success = False
        result.denies.append("conftest timed out")
    except Exception as e:
        result.success = False
        result.denies.append(f"conftest error: {e}")

    return result


async def prepare_target(
    target: K8sTarget | None = None,
    dry_run: bool = False,
) -> PrepareResult:
    """Prepare K8s target: validate and write configuration.

    Args:
        target: Target to prepare (auto-detected if None)
        dry_run: If True, validate but don't write config

    Returns:
        PrepareResult with validation and config details
    """
    # Auto-detect target if not specified
    if target is None:
        target = detect_target()

    if target == K8sTarget.NONE:
        return PrepareResult(
            success=False,
            target=target,
            validation=ValidationResult(
                success=False,
                target=target,
                denies=["No K8s target detected. Set CYBERSEC_K8S_TARGET or KUBECONFIG."],
            ),
            message="No target detected",
        )

    # Gather runtime config
    runtime_config = await gather_runtime_config()

    # Validate target requirements
    validation = await validate_target(target, runtime_config)

    if not validation.success:
        return PrepareResult(
            success=False,
            target=target,
            validation=validation,
            message=f"Validation failed: {len(validation.denies)} issues found",
        )

    # Generate target-specific config
    if target == K8sTarget.AWS:
        config = _generate_aws_config(runtime_config)
    elif target == K8sTarget.RKE2:
        config = _generate_rke2_config(runtime_config)
    elif target == K8sTarget.K3D:
        config = _generate_k3d_config(runtime_config)
    else:
        config = {}

    # Write config file unless dry-run
    config_path = None
    if not dry_run:
        config_dir = Path(".cybersec/k8s")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{target.value}-target.toml"
        _write_toml_config(config_path, config)

    return PrepareResult(
        success=True,
        target=target,
        validation=validation,
        config_path=str(config_path) if config_path else None,
        config=config,
        message=f"Target {target.value} prepared successfully",
    )


def _flatten_for_policy(runtime_config: dict[str, Any]) -> dict[str, Any]:
    """Flatten runtime config for policy consumption.

    The policy files expect a flat structure with keys like:
    - tools.kubectl
    - kubernetes.target
    - aws.credentials_configured
    """
    return {
        "platform": runtime_config.get("platform", {}),
        "tools": runtime_config.get("tools", {}),
        "kubernetes": runtime_config.get("kubernetes", {}),
        "aws": runtime_config.get("aws", {}),
        "services": runtime_config.get("services", {}),
        "developer": runtime_config.get("developer", {}),
        "paths": runtime_config.get("paths", {}),
        "node_resources": runtime_config.get("node_resources", {}),
        "zarf_local": runtime_config.get("zarf_local", {}),
    }


def _generate_aws_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
    """Generate AWS target configuration."""
    aws = runtime_config.get("aws", {})
    ngrok = runtime_config.get("services", {}).get("ngrok", {})

    return {
        "target": {
            "type": "aws",
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        },
        "aws": {
            "account_id": aws.get("account_id"),
            "region": os.environ.get("AWS_REGION", "us-west-2"),
        },
        "dask": {
            "worker_replicas": 64,
            "worker_memory_limit": "8GB",
            "worker_cpu_limit": "2",
        },
        "jupyterhub": {
            "enabled": True,
            "user_storage_size": "10Gi",
        },
        "ngrok": {
            "dask_domain": ngrok.get("domains", {}).get("dask", "dask.zndx.org"),
            "jupyterhub_domain": ngrok.get("domains", {}).get("jupyterhub", "jupyter.zndx.org"),
        },
        "infrastructure": {
            "iac_tool": runtime_config.get("tools", {}).get("iac_tool", "tofu"),
            "ssh_key": "~/.ssh/cybersec.pem",
        },
    }


def _generate_rke2_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
    """Generate RKE2 target configuration."""
    k8s = runtime_config.get("kubernetes", {})

    return {
        "target": {
            "type": "rke2",
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        },
        "kubeconfig": {
            "path": k8s.get("kubeconfig_path", "~/.kube/rke2.yaml"),
        },
        "dask": {
            "worker_replicas": 4,
            "worker_memory_limit": "4GB",
            "worker_cpu_limit": "1",
        },
        "jupyterhub": {
            "enabled": True,
            "user_storage_size": "5Gi",
        },
    }


def _generate_k3d_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
    """Generate k3d target configuration."""
    return {
        "target": {
            "type": "k3d",
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        },
        "cluster": {
            "name": os.environ.get("K3D_CLUSTER_NAME", "cybersec"),
            "agents": 1,  # Number of worker nodes
        },
        "dask": {
            "worker_replicas": 1,
            "worker_memory_limit": "2GB",
            "worker_cpu_limit": "1",
        },
        "jupyterhub": {
            "enabled": True,
            "user_storage_size": "2Gi",
        },
    }


def _write_toml_config(path: Path, config: dict[str, Any]) -> None:
    """Write configuration as TOML file."""
    lines = []

    def write_section(data: dict[str, Any], prefix: str = "") -> None:
        for key, value in data.items():
            if isinstance(value, dict):
                section_name = f"{prefix}.{key}" if prefix else key
                lines.append(f"\n[{section_name}]")
                write_section(value, section_name)
            elif isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, int):
                lines.append(f"{key} = {value}")
            elif isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            elif value is None:
                lines.append(f"# {key} = <not set>")

    for key, value in config.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for k, v in value.items():
                if isinstance(v, dict):
                    lines.append(f"\n[{key}.{k}]")
                    for kk, vv in v.items():
                        if isinstance(vv, bool):
                            lines.append(f"{kk} = {'true' if vv else 'false'}")
                        elif isinstance(vv, int):
                            lines.append(f"{kk} = {vv}")
                        elif isinstance(vv, str):
                            lines.append(f'{kk} = "{vv}"')
                        elif vv is None:
                            lines.append(f"# {kk} = <not set>")
                elif isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, int):
                    lines.append(f"{k} = {v}")
                elif isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                elif v is None:
                    lines.append(f"# {k} = <not set>")
        lines.append("")

    path.write_text("\n".join(lines))
