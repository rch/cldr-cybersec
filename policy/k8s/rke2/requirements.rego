# RKE2 K8s Target Requirements Policy
#
# Validates requirements for RKE2/Rancher cluster deployment.
# This is for local RKE2 clusters (workstation or on-prem).
#
# Run with: conftest test build/environment.json --policy policy/k8s/rke2/
#
# Config structure:
#   input.tools - Tool availability
#   input.kubernetes - Kubernetes configuration

package k8s.rke2.requirements

import rego.v1

# Access sections from environment.json
tools := input.tools
k8s := input.kubernetes

# ==========================================================================
# KUBECONFIG Validation
# ==========================================================================

# Deny if KUBECONFIG not set for RKE2 target
deny contains msg if {
    k8s.target == "rke2"
    k8s.kubeconfig_path == ""
    msg := "KUBECONFIG not set for RKE2 target. Set: export KUBECONFIG=~/.kube/rke2.yaml"
}

# Deny if kubeconfig exists but cluster type is not RKE2
deny contains msg if {
    k8s.target == "rke2"
    k8s.kubeconfig_exists
    k8s.cluster_type != "rke2"
    k8s.cluster_type != "none"
    msg := sprintf("Target is RKE2 but KUBECONFIG points to %s cluster. Update KUBECONFIG path.", [k8s.cluster_type])
}

# Deny if kubectl cannot connect to RKE2 cluster
deny contains msg if {
    k8s.target == "rke2"
    k8s.kubeconfig_exists
    tools.kubectl
    not k8s.kubectl_connected
    msg := "Cannot connect to RKE2 cluster. Check if cluster is running and network is accessible."
}

# ==========================================================================
# Tool Validation
# ==========================================================================

# Deny if kubectl not available
deny contains msg if {
    not tools.kubectl
    msg := "kubectl not installed. Required for RKE2 cluster management."
}

# Deny if helm not available
deny contains msg if {
    not tools.helm
    msg := "helm not installed. Required for deploying Dask and JupyterHub."
}

# ==========================================================================
# Info: Show RKE2 configuration
# ==========================================================================

# Info: Show kubeconfig path
info contains msg if {
    k8s.target == "rke2"
    k8s.kubeconfig_exists
    msg := sprintf("RKE2 kubeconfig: %s", [k8s.kubeconfig_path])
}

# Info: Show cluster connection status
info contains msg if {
    k8s.target == "rke2"
    k8s.kubectl_connected
    msg := "Connected to RKE2 cluster"
}

# Info: Ready for deployment
info contains msg if {
    k8s.target == "rke2"
    k8s.kubectl_connected
    tools.helm
    msg := "RKE2 target ready for Dask/JupyterHub deployment"
}
