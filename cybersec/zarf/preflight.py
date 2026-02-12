"""Zarf air-gap preflight validation.

Validates requirements for deploying the Zarf package to RKE2 clusters:
- Tool availability (zarf, kubectl, helm)
- Cluster connectivity and type
- StorageClass configuration
- Registry availability
- Ingress controller presence
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ZarfPreflightResult:
    """Result from Zarf preflight validation."""

    success: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    denies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    def add_check(
        self,
        name: str,
        passed: bool,
        message: str,
        category: str = "info",
    ) -> None:
        """Add a check result.

        Args:
            name: Check name
            passed: Whether check passed
            message: Human-readable message
            category: deny, warn, or info
        """
        self.checks.append({
            "name": name,
            "passed": passed,
            "message": message,
            "category": category,
        })

        if not passed:
            if category == "deny":
                self.denies.append(message)
                self.success = False
            elif category == "warn":
                self.warnings.append(message)
        else:
            if category == "info":
                self.infos.append(message)


async def run_preflight_checks(
    check_registry: bool = False,
    registry_url: str | None = None,
) -> ZarfPreflightResult:
    """Run all preflight checks for Zarf deployment.

    Args:
        check_registry: If True, validate registry connectivity
        registry_url: External registry URL to check (if provided)

    Returns:
        ZarfPreflightResult with all check results
    """
    result = ZarfPreflightResult(success=True)

    # Tool checks
    _check_zarf_installed(result)
    _check_kubectl_installed(result)
    _check_helm_installed(result)
    _check_container_runtime(result)

    # Cluster checks
    await _check_cluster_connectivity(result)
    await _check_cluster_type(result)
    await _check_storage_class(result)
    await _check_ingress_controller(result)

    # Registry checks (optional)
    if check_registry:
        await _check_registry(result, registry_url)

    # Resource checks
    await _check_namespace_quotas(result)

    return result


def _check_zarf_installed(result: ZarfPreflightResult) -> None:
    """Check if zarf CLI is installed."""
    zarf_path = shutil.which("zarf")
    if zarf_path:
        # Get version
        try:
            proc = subprocess.run(
                ["zarf", "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = proc.stdout.strip() if proc.returncode == 0 else "unknown"
            result.add_check(
                "zarf",
                True,
                f"zarf installed: {version}",
                "info",
            )
        except Exception:
            result.add_check(
                "zarf",
                True,
                f"zarf installed at {zarf_path}",
                "info",
            )
    else:
        result.add_check(
            "zarf",
            False,
            "zarf not installed. Install: brew install defenseunicorns/tap/zarf",
            "deny",
        )


def _check_kubectl_installed(result: ZarfPreflightResult) -> None:
    """Check if kubectl is installed."""
    kubectl_path = shutil.which("kubectl")
    if kubectl_path:
        result.add_check(
            "kubectl",
            True,
            f"kubectl installed at {kubectl_path}",
            "info",
        )
    else:
        result.add_check(
            "kubectl",
            False,
            "kubectl not installed. Required for cluster management.",
            "deny",
        )


def _check_helm_installed(result: ZarfPreflightResult) -> None:
    """Check if helm is installed."""
    helm_path = shutil.which("helm")
    if helm_path:
        result.add_check(
            "helm",
            True,
            f"helm installed at {helm_path}",
            "info",
        )
    else:
        result.add_check(
            "helm",
            False,
            "helm not installed. Required for chart deployment.",
            "deny",
        )


def _check_container_runtime(result: ZarfPreflightResult) -> None:
    """Check if a container runtime (podman/docker) is available."""
    podman_path = shutil.which("podman")
    docker_path = shutil.which("docker")

    if podman_path:
        result.add_check(
            "container_runtime",
            True,
            f"podman available at {podman_path}",
            "info",
        )
    elif docker_path:
        result.add_check(
            "container_runtime",
            True,
            f"docker available at {docker_path}",
            "info",
        )
    else:
        result.add_check(
            "container_runtime",
            False,
            "No container runtime found. Install podman or docker for image building.",
            "warn",
        )


async def _check_cluster_connectivity(result: ZarfPreflightResult) -> None:
    """Check if kubectl can connect to the cluster."""
    kubeconfig = os.environ.get("KUBECONFIG", "")
    if not kubeconfig:
        kubeconfig = str(Path.home() / ".kube" / "config")

    if not Path(kubeconfig).exists():
        result.add_check(
            "kubeconfig",
            False,
            f"KUBECONFIG not found: {kubeconfig}",
            "deny",
        )
        return

    result.add_check(
        "kubeconfig",
        True,
        f"KUBECONFIG: {kubeconfig}",
        "info",
    )

    # Test connectivity
    try:
        proc = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            result.add_check(
                "cluster_connectivity",
                True,
                "Connected to Kubernetes cluster",
                "info",
            )
        else:
            result.add_check(
                "cluster_connectivity",
                False,
                f"Cannot connect to cluster: {proc.stderr.strip()}",
                "deny",
            )
    except subprocess.TimeoutExpired:
        result.add_check(
            "cluster_connectivity",
            False,
            "Cluster connection timed out",
            "deny",
        )
    except Exception as e:
        result.add_check(
            "cluster_connectivity",
            False,
            f"Cluster connection error: {e}",
            "deny",
        )


async def _check_cluster_type(result: ZarfPreflightResult) -> None:
    """Check if cluster is RKE2 (recommended for air-gap)."""
    try:
        # Check for RKE2-specific resources
        proc = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "jsonpath={.items[0].status.nodeInfo.kubeletVersion}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            version = proc.stdout.strip()
            if "rke2" in version.lower():
                result.add_check(
                    "cluster_type",
                    True,
                    f"RKE2 cluster detected: {version}",
                    "info",
                )
            elif "k3s" in version.lower():
                result.add_check(
                    "cluster_type",
                    True,
                    f"K3s cluster detected: {version} (compatible)",
                    "warn",
                )
            else:
                result.add_check(
                    "cluster_type",
                    True,
                    f"Cluster version: {version}",
                    "info",
                )
    except Exception:
        pass  # Not critical, skip if can't determine


async def _check_storage_class(result: ZarfPreflightResult) -> None:
    """Check if a default StorageClass exists (required for JupyterHub PVCs)."""
    try:
        proc = subprocess.run(
            ["kubectl", "get", "storageclass", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            storage_classes = data.get("items", [])

            if not storage_classes:
                result.add_check(
                    "storage_class",
                    False,
                    "No StorageClass found. JupyterHub PVCs will fail.",
                    "deny",
                )
                return

            # Check for default
            default_sc = None
            for sc in storage_classes:
                annotations = sc.get("metadata", {}).get("annotations", {})
                if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                    default_sc = sc.get("metadata", {}).get("name")
                    break

            if default_sc:
                result.add_check(
                    "storage_class",
                    True,
                    f"Default StorageClass: {default_sc}",
                    "info",
                )
            else:
                sc_names = [sc.get("metadata", {}).get("name") for sc in storage_classes]
                result.add_check(
                    "storage_class",
                    True,
                    f"StorageClasses available: {', '.join(sc_names)} (no default)",
                    "warn",
                )
    except Exception as e:
        result.add_check(
            "storage_class",
            False,
            f"Cannot check StorageClass: {e}",
            "warn",
        )


async def _check_ingress_controller(result: ZarfPreflightResult) -> None:
    """Check if an ingress controller is available."""
    try:
        # Check for Traefik (RKE2 default)
        proc = subprocess.run(
            ["kubectl", "get", "ingressclass", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            ingress_classes = data.get("items", [])

            if ingress_classes:
                ic_names = [ic.get("metadata", {}).get("name") for ic in ingress_classes]
                result.add_check(
                    "ingress_controller",
                    True,
                    f"IngressClasses: {', '.join(ic_names)}",
                    "info",
                )
            else:
                result.add_check(
                    "ingress_controller",
                    True,
                    "No IngressClass found. Services accessible via NodePort only.",
                    "warn",
                )
    except Exception:
        result.add_check(
            "ingress_controller",
            True,
            "Cannot check IngressClass. Services accessible via NodePort.",
            "warn",
        )


async def _check_registry(
    result: ZarfPreflightResult,
    registry_url: str | None = None,
) -> None:
    """Check registry availability.

    If registry_url is provided, check connectivity.
    Otherwise, note that Zarf will deploy its internal registry.
    """
    if registry_url:
        # Check external registry
        try:
            # Try to reach registry with curl
            proc = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"https://{registry_url}/v2/"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.stdout.strip() in ("200", "401"):
                result.add_check(
                    "registry",
                    True,
                    f"External registry reachable: {registry_url}",
                    "info",
                )
            else:
                result.add_check(
                    "registry",
                    False,
                    f"Cannot reach registry: {registry_url} (HTTP {proc.stdout.strip()})",
                    "warn",
                )
        except Exception as e:
            result.add_check(
                "registry",
                False,
                f"Cannot reach registry: {registry_url} ({e})",
                "warn",
            )
    else:
        result.add_check(
            "registry",
            True,
            "No external registry configured. Zarf will deploy internal registry.",
            "info",
        )


async def _check_namespace_quotas(result: ZarfPreflightResult) -> None:
    """Check if there are restrictive namespace quotas."""
    try:
        # Check for cluster-wide resource quotas
        proc = subprocess.run(
            ["kubectl", "get", "resourcequota", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            quotas = data.get("items", [])

            if quotas:
                quota_namespaces = list({q.get("metadata", {}).get("namespace") for q in quotas})
                result.add_check(
                    "resource_quotas",
                    True,
                    f"ResourceQuotas in namespaces: {', '.join(quota_namespaces)}",
                    "warn",
                )
            else:
                result.add_check(
                    "resource_quotas",
                    True,
                    "No ResourceQuotas found",
                    "info",
                )
    except Exception:
        pass  # Not critical


def get_package_info() -> dict[str, Any]:
    """Get information about the Zarf package.

    Returns:
        Dict with package metadata and component info
    """
    project_root = Path(__file__).parent.parent.parent
    zarf_yaml = project_root / "zarf" / "zarf.yaml"

    if not zarf_yaml.exists():
        return {"error": "zarf.yaml not found"}

    # Parse zarf.yaml (simplified - YAML parsing)
    # For a complete implementation, use pyyaml
    try:
        import yaml
        with open(zarf_yaml) as f:
            data = yaml.safe_load(f)
        return {
            "name": data.get("metadata", {}).get("name"),
            "version": data.get("metadata", {}).get("version"),
            "description": data.get("metadata", {}).get("description"),
            "components": [
                {
                    "name": c.get("name"),
                    "required": c.get("required", False),
                    "default": c.get("default", True),
                    "description": c.get("description", ""),
                }
                for c in data.get("components", [])
            ],
            "variables": [
                {
                    "name": v.get("name"),
                    "default": v.get("default", ""),
                    "description": v.get("description", ""),
                    "sensitive": v.get("sensitive", False),
                }
                for v in data.get("variables", [])
            ],
        }
    except ImportError:
        return {"error": "pyyaml not installed for package parsing"}
    except Exception as e:
        return {"error": str(e)}
