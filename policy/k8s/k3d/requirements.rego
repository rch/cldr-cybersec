# k3d K8s Target Requirements Policy
#
# Validates requirements for k3d local development clusters.
# k3d is a lightweight wrapper around k3s for local Kubernetes.
#
# Run with: conftest test build/environment.json --policy policy/k8s/k3d/
#
# Config structure:
#   input.tools - Tool availability
#   input.kubernetes - Kubernetes configuration
#   input.platform - Platform detection (macOS, Linux)

package k8s.k3d.requirements

import rego.v1

# Access sections from environment.json
tools := input.tools
k8s := input.kubernetes
platform := input.platform

# ==========================================================================
# k3d Tool Validation
# ==========================================================================

# Deny if k3d not installed
deny contains msg if {
    k8s.target == "k3d"
    not tools.k3d
    msg := "k3d not installed. Install via: brew install k3d (macOS) or curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash"
}

# ==========================================================================
# Container Runtime Validation (macOS)
# ==========================================================================

# Deny if on macOS but no container runtime available
deny contains msg if {
    k8s.target == "k3d"
    platform.is_macos
    not tools.docker
    not tools.podman
    msg := "No container runtime found on macOS. Install: brew install podman or Docker Desktop"
}

# Warn if using Docker Desktop on macOS (podman preferred for license reasons)
warn contains msg if {
    k8s.target == "k3d"
    platform.is_macos
    tools.docker
    not tools.podman
    msg := "Using Docker Desktop. Consider podman for license-free local development: brew install podman"
}

# Deny if podman is available but machine not running (macOS)
deny contains msg if {
    k8s.target == "k3d"
    platform.is_macos
    tools.podman
    not tools.podman_machine_running
    msg := "Podman machine not running. Start with: podman machine start"
}

# ==========================================================================
# Container Runtime Validation (Linux)
# ==========================================================================

# Deny if on Linux but no container runtime
deny contains msg if {
    k8s.target == "k3d"
    platform.is_linux
    not tools.docker
    not tools.podman
    msg := "No container runtime found. Install: apt install podman or docker.io"
}

# ==========================================================================
# k3d Cluster State
# ==========================================================================

# Info: k3d needs provisioning
info contains msg if {
    k8s.target == "k3d"
    k8s.needs_k3d_provisioning
    msg := "k3d cluster will be created (no existing kubeconfig)"
}

# Info: k3d cluster already exists
info contains msg if {
    k8s.target == "k3d"
    k8s.kubeconfig_exists
    k8s.cluster_type == "k3d"
    msg := sprintf("Existing k3d cluster found: %s", [k8s.kubeconfig_path])
}

# Info: k3d ready
info contains msg if {
    k8s.target == "k3d"
    tools.k3d
    tools.kubectl
    tools.helm
    msg := "k3d target ready for cluster provisioning"
}

# ==========================================================================
# Resource Recommendations
# ==========================================================================

# Warn about resource usage for Dask
warn contains msg if {
    k8s.target == "k3d"
    msg := "k3d local cluster: Dask worker replicas limited by local resources. Consider rke2 or aws for production workloads."
}
