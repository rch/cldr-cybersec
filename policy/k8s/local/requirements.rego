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
