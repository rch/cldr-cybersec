# Local Zarf Deployment Requirements Policy
#
# Validates requirements for deploying the Dask+JupyterHub+Panel-Viz stack
# locally via Zarf to an RKE2 or k3d cluster running alongside the Flink
# devenv stack.
#
# Run with: conftest test build/environment.json --policy policy/k8s/local/
#
# Config structure:
#   input.tools          - Tool availability
#   input.kubernetes     - Kubernetes configuration
#   input.node_resources - Node memory/disk/storage info
#   input.zarf_local     - Zarf local deployment config

package k8s.local.requirements

import rego.v1

# Access sections from environment.json
tools := input.tools
k8s := input.kubernetes
nodes := input.node_resources
zarf := input.zarf_local
services := input.services

# ==========================================================================
# Tool Requirements (DENY - blocking)
# ==========================================================================

deny contains msg if {
    not tools.zarf
    msg := "zarf not installed. Install: brew install defenseunicorns/tap/zarf"
}

deny contains msg if {
    not tools.kubectl
    msg := "kubectl not installed. Required for cluster management."
}

deny contains msg if {
    not tools.helm
    msg := "helm not installed. Required for deploying Dask operator and JupyterHub."
}

# ==========================================================================
# Kubeconfig Requirements (DENY - blocking)
# ==========================================================================

deny contains msg if {
    k8s.kubeconfig_path == ""
    msg := "No kubeconfig detected. Set KUBECONFIG or use devenv k3d provisioning."
}

deny contains msg if {
    k8s.kubeconfig_path != ""
    not k8s.kubeconfig_exists
    msg := sprintf("KUBECONFIG file not found: %s", [k8s.kubeconfig_path])
}

# Kubeconfig exists but is not readable (permission denied)
deny contains msg if {
    k8s.kubeconfig_exists
    k8s.kubeconfig_error == "permission_denied"
    msg := sprintf(
        "KUBECONFIG %s is not readable (permission denied). Fix with:\n  sudo cp %s ~/.kube/rke2.yaml && sudo chown $(id -u):$(id -g) ~/.kube/rke2.yaml\n  export KUBECONFIG=~/.kube/rke2.yaml",
        [k8s.kubeconfig_path, k8s.kubeconfig_path],
    )
}

# Kubeconfig readable but kubectl cannot connect (network/cluster issue)
deny contains msg if {
    k8s.kubeconfig_exists
    k8s.kubeconfig_readable
    tools.kubectl
    not k8s.kubectl_connected
    msg := sprintf(
        "Cannot connect to cluster via %s. Check:\n  1. Cluster is running: systemctl status rke2-server\n  2. Network is reachable: kubectl --kubeconfig=%s cluster-info",
        [k8s.kubeconfig_path, k8s.kubeconfig_path],
    )
}

# Cluster type must be rke2 or k3d for local deployment
deny contains msg if {
    k8s.kubectl_connected
    k8s.cluster_type != "rke2"
    k8s.cluster_type != "k3d"
    k8s.cluster_type != "k3s"
    msg := sprintf("Target must be rke2 or k3d for local deployment (detected: %s)", [k8s.cluster_type])
}

# ==========================================================================
# Package Requirements (DENY - blocking)
# ==========================================================================

deny contains msg if {
    not zarf.init_package_exists
    msg := "Zarf init package not found in zarf/. Download with: zarf tools download-init"
}

# ==========================================================================
# MinIO Requirements (DENY - blocking)
# ==========================================================================

deny contains msg if {
    not services.minio.healthy
    msg := "MinIO not running. Local deployment requires MinIO for S3. Start: devenv up -d"
}

# ==========================================================================
# Memory Requirements (DENY - blocking)
# ==========================================================================

# Total system RAM must be at least 19 GB (7 GB Flink + 12 GB minimum K8s)
deny contains msg if {
    nodes.total_memory_gb > 0
    nodes.total_memory_gb < 19
    msg := sprintf(
        "Insufficient RAM: %d GB total. Need at least 19 GB (7 GB Flink + 12 GB K8s). Consider closing other workloads.",
        [nodes.total_memory_gb],
    )
}

# ==========================================================================
# Memory Warnings (WARN - non-blocking)
# ==========================================================================

# Available K8s memory is tight (12-16 GB)
warn contains msg if {
    nodes.total_memory_gb > 0
    avail := nodes.total_memory_gb - 7
    avail >= 12
    avail < 16
    msg := sprintf(
        "Tight memory: ~%d GB available for K8s (total %d GB - 7 GB Flink). Recommend DASK_WORKER_REPLICAS=1.",
        [avail, nodes.total_memory_gb],
    )
}

# ==========================================================================
# Disk Warnings (WARN - non-blocking)
# ==========================================================================

warn contains msg if {
    nodes.disk_pressure
    msg := "DiskPressure condition/taint detected on cluster node. Free disk space or use a separate mount (e.g. /raid/rke2/)."
}

warn contains msg if {
    nodes.disk_free_pct > 0
    nodes.disk_free_pct < 15
    msg := sprintf(
        "Root disk free is only %d%%. If a separate mount is available (e.g. /raid/rke2/), set DASK_SPILL_DIR to use it for worker spill data.",
        [nodes.disk_free_pct],
    )
}

warn contains msg if {
    zarf.spill_dir != ""
    not zarf.spill_dir_exists
    msg := sprintf(
        "DASK_SPILL_DIR=%s does not exist. Create it or unset to use emptyDir:\n  sudo mkdir -p %s && sudo chown $(id -u):$(id -g) %s",
        [zarf.spill_dir, zarf.spill_dir, zarf.spill_dir],
    )
}

# ==========================================================================
# Info: Resource Sizing Recommendations
# ==========================================================================

# Recommended worker count based on available RAM
info contains msg if {
    nodes.total_memory_gb > 0
    avail := nodes.total_memory_gb - 7
    avail >= 25
    msg := sprintf(
        "Recommended: 4 Dask workers (%d GB available for K8s)",
        [avail],
    )
}

info contains msg if {
    nodes.total_memory_gb > 0
    avail := nodes.total_memory_gb - 7
    avail >= 20
    avail < 25
    msg := sprintf(
        "Recommended: 3 Dask workers (%d GB available for K8s)",
        [avail],
    )
}

info contains msg if {
    nodes.total_memory_gb > 0
    avail := nodes.total_memory_gb - 7
    avail >= 16
    avail < 20
    msg := sprintf(
        "Recommended: 2 Dask workers (%d GB available for K8s)",
        [avail],
    )
}

info contains msg if {
    nodes.total_memory_gb > 0
    avail := nodes.total_memory_gb - 7
    avail >= 12
    avail < 16
    msg := sprintf(
        "Recommended: 1 Dask worker (%d GB available for K8s)",
        [avail],
    )
}

# Detected cluster info
info contains msg if {
    k8s.kubectl_connected
    k8s.cluster_type != "none"
    msg := sprintf("Cluster type: %s", [k8s.cluster_type])
}

# Cluster type inferred from path even without read access
info contains msg if {
    not k8s.kubectl_connected
    k8s.cluster_type != "none"
    msg := sprintf("Cluster type (inferred): %s", [k8s.cluster_type])
}

info contains msg if {
    k8s.kubeconfig_exists
    msg := sprintf("KUBECONFIG: %s", [k8s.kubeconfig_path])
}

info contains msg if {
    tools.zarf
    tools.zarf_version != ""
    msg := sprintf("zarf version: %s", [tools.zarf_version])
}

# Spill dir status
info contains msg if {
    zarf.spill_dir != ""
    zarf.spill_dir_exists
    msg := sprintf("Spill dir: hostPath %s", [zarf.spill_dir])
}

info contains msg if {
    zarf.spill_dir == ""
    msg := "Spill dir: emptyDir (disk-light). For hostPath spill: export DASK_SPILL_DIR=/raid/rke2/dask-spill"
}

# Storage classes
info contains msg if {
    nodes.default_storage_class != ""
    msg := sprintf("Default StorageClass: %s", [nodes.default_storage_class])
}

# MinIO status
info contains msg if {
    services.minio.healthy
    msg := sprintf("MinIO healthy at %s (bucket: cybersec)", [zarf.minio_endpoint])
}

# Ready for deployment
info contains msg if {
    tools.zarf
    tools.kubectl
    tools.helm
    k8s.kubectl_connected
    nodes.total_memory_gb >= 19
    services.minio.healthy
    msg := "Ready for local Zarf deployment."
}

# ==========================================================================
# Zarf Registry State (DENY/WARN/INFO)
# Detects stale state from failed `zarf init` attempts.
# Data source: input.zarf_local.zarf_registry (gathered by local.py)
# ==========================================================================

registry := zarf.zarf_registry

# DENY: PVC in Lost phase — blocks init, must recover first
deny contains msg if {
    registry.pvc_exists
    registry.pvc_phase == "Lost"
    msg := concat("", [
        "Zarf registry PVC is in Lost phase — a previous `zarf init` failed and left stale state.\n",
        "  Recovery: ./zarf/scripts/zarf-init-recovery.sh\n",
        "  Or manually: patch finalizers null, force-delete PVC+PV, delete zarf namespace, then retry init.",
    ])
}

# WARN: PVC in Pending phase — may indicate missing PV or size mismatch
warn contains msg if {
    registry.pvc_exists
    registry.pvc_phase == "Pending"
    msg := concat("", [
        "Zarf registry PVC is Pending — likely no matching PV or size mismatch.\n",
        "  Check: kubectl get pv zarf-registry-pv\n",
        "  Fix: create a PV with claimRef pre-binding (see zarf/README.md).",
    ])
}

# WARN: PV exists without claimRef — won't auto-bind to PVC
warn contains msg if {
    registry.pv_exists
    not registry.pv_has_claim_ref
    msg := concat("", [
        "Zarf registry PV exists but has no claimRef — it won't auto-bind to the PVC.\n",
        "  Fix: delete PV and recreate with claimRef, or run ./zarf/scripts/zarf-init-recovery.sh",
    ])
}

# WARN: Stuck finalizers on PVC or PV
warn contains msg if {
    registry.has_stuck_finalizers
    msg := concat("", [
        "Zarf registry PVC/PV has stuck finalizers — cleanup required before re-init.\n",
        "  Fix: kubectl patch pvc zarf-docker-registry -n zarf -p '{\"metadata\":{\"finalizers\":null}}'\n",
        "  Or run: ./zarf/scripts/zarf-init-recovery.sh",
    ])
}

# INFO: Show current PVC/PV state when present
info contains msg if {
    registry.pvc_exists
    registry.pvc_phase == "Bound"
    msg := sprintf("Zarf registry PVC: Bound (volume: %s)", [registry.pvc_volume_name])
}

info contains msg if {
    registry.pv_exists
    registry.pv_phase != ""
    msg := sprintf("Zarf registry PV: %s", [registry.pv_phase])
}

info contains msg if {
    registry.namespace_exists
    not registry.pvc_exists
    msg := "Zarf namespace exists but no registry PVC — init may be incomplete."
}
