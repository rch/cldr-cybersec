# Kubernetes Environment Policy
#
# Validates Kubernetes configuration consistency.
# Run with: conftest test build/environment.json --policy policy/environment/
#
# Config structure:
#   input.effective.kubernetes - Merged static + runtime kubernetes config
#   input.static.cybersec.kubernetes - Static kubernetes configuration

package environment.kubernetes

import rego.v1

# Use effective config (merged static + runtime)
k8s := input.effective.kubernetes

# Deny: K8s enabled but trying to provision k3d when KUBECONFIG points to RKE2
# This prevents accidentally creating a local k3d cluster when you meant to use RKE2
deny contains msg if {
    k8s.enabled == true
    k8s.needs_k3d_provisioning == true
    k8s.cluster_type == "rke2"
    msg := "K8s enabled with k3d provisioning but KUBECONFIG points to RKE2. Set CYBERSEC_K8S_TARGET=rke2 explicitly."
}

# Deny: K8s enabled but no valid target and no kubeconfig
deny contains msg if {
    k8s.enabled == true
    k8s.target == "none"
    k8s.kubeconfig_exists == false
    k8s.kubectl_available == false
    msg := "K8s enabled but no kubectl available and no kubeconfig found. Install kubectl first."
}

# Warn: K8s enabled with target=rke2 but kubeconfig points to k3d
# User might have stale kubeconfig from previous k3d session
warn contains msg if {
    k8s.enabled == true
    k8s.target == "rke2"
    k8s.cluster_type == "k3d"
    msg := "Target is RKE2 but KUBECONFIG points to k3d cluster. Update KUBECONFIG to point to RKE2."
}

# Warn: K8s enabled but KUBECONFIG set to non-existent file
warn contains msg if {
    k8s.enabled == true
    k8s.kubeconfig_path != ""
    k8s.kubeconfig_exists == false
    msg := sprintf("KUBECONFIG set to '%s' but file does not exist", [k8s.kubeconfig_path])
}

# Warn: K8s enabled but kubectl not available
warn contains msg if {
    k8s.enabled == true
    k8s.kubectl_available == false
    msg := "K8s enabled but kubectl not found in PATH"
}

# Warn: K8s enabled with kubeconfig but kubectl cannot connect
warn contains msg if {
    k8s.enabled == true
    k8s.kubeconfig_exists == true
    k8s.kubectl_available == true
    k8s.kubectl_connected == false
    msg := "K8s enabled but kubectl cannot connect to cluster. Check if cluster is running."
}

# Info: Show kubernetes target when enabled
info contains msg if {
    k8s.enabled == true
    k8s.target != "none"
    msg := sprintf("Kubernetes target: %s", [k8s.target])
}

# Info: Show k3d provisioning status
info contains msg if {
    k8s.enabled == true
    k8s.needs_k3d_provisioning == true
    msg := "k3d cluster will be auto-provisioned (no existing kubeconfig)"
}

# Info: K8s stack is disabled (default)
info contains msg if {
    k8s.enabled == false
    msg := "K8s stack disabled (default). Set ENABLE_K8S=true to enable Dask/JupyterHub."
}

# Info: Using external kubeconfig (RKE2 or pre-existing k3d)
info contains msg if {
    k8s.enabled == true
    k8s.kubeconfig_exists == true
    k8s.kubectl_connected == true
    msg := sprintf("Using existing kubeconfig: %s (cluster type: %s)", [k8s.kubeconfig_path, k8s.cluster_type])
}
