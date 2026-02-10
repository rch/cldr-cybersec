# Base K8s Environment Policy
#
# Validates common Kubernetes requirements across all targets.
# Run with: conftest test build/environment.json --policy policy/k8s/
#
# Config structure:
#   input.tools - Tool availability from runtime detection
#   input.kubernetes - Kubernetes configuration

package k8s.base

import rego.v1

# Access tool checks from environment.json
tools := input.tools

# Access kubernetes config
k8s := input.kubernetes

# ==========================================================================
# Required Tool Validation
# ==========================================================================

# Deny if kubectl is not installed
deny contains msg if {
    not tools.kubectl
    msg := "kubectl not installed. Install via: brew install kubectl (macOS) or apt install kubectl (Linux)"
}

# Deny if helm is not installed
deny contains msg if {
    not tools.helm
    msg := "helm not installed. Install via: brew install helm (macOS) or snap install helm (Linux)"
}

# ==========================================================================
# Kubeconfig Validation
# ==========================================================================

# Deny if kubeconfig set but file doesn't exist
deny contains msg if {
    k8s.kubeconfig_path != ""
    not k8s.kubeconfig_exists
    msg := sprintf("KUBECONFIG set to '%s' but file does not exist", [k8s.kubeconfig_path])
}

# Warn if kubeconfig exists but cluster is not reachable
warn contains msg if {
    k8s.kubeconfig_exists
    tools.kubectl
    not k8s.kubectl_connected
    msg := "kubectl cannot connect to cluster. Check if cluster is running and kubeconfig is valid."
}

# ==========================================================================
# Info: Show detected configuration
# ==========================================================================

# Info: Show kubectl availability
info contains msg if {
    tools.kubectl
    msg := "kubectl installed and available"
}

# Info: Show helm availability
info contains msg if {
    tools.helm
    msg := "helm installed and available"
}

# Info: Show cluster connection status
info contains msg if {
    k8s.kubectl_connected
    msg := sprintf("Connected to cluster (type: %s)", [k8s.cluster_type])
}

# Info: Show target detection
info contains msg if {
    k8s.target != "none"
    msg := sprintf("K8s target: %s", [k8s.target])
}
