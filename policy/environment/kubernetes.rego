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

# Deny: k3d enabled but KUBECONFIG points to RKE2 cluster
# This prevents accidentally running local k3d processes when connected to production
deny contains msg if {
    k8s.k3d_enabled == true
    k8s.cluster_type == "rke2"
    msg := "k3d enabled (ENABLE_K3D=true) but KUBECONFIG points to RKE2 cluster. Unset ENABLE_K3D or change KUBECONFIG."
}

# Deny: k3d enabled but target is rke2
# Similar check using the detected target
deny contains msg if {
    k8s.k3d_enabled == true
    k8s.target == "rke2"
    msg := "k3d enabled but kubernetes target is RKE2. Local k3d processes would conflict with remote cluster."
}

# Warn: Dask configuration present but no Kubernetes target
warn contains msg if {
    input.static.cybersec.kubernetes.dask.scheduler_port
    k8s.target == "none"
    k8s.k3d_enabled == false
    msg := "Dask configuration present but no Kubernetes target. Set ENABLE_K3D=true for local development or configure KUBECONFIG for remote cluster."
}

# Warn: KUBECONFIG set but file does not exist
warn contains msg if {
    k8s.kubeconfig_path != ""
    k8s.kubeconfig_exists == false
    msg := sprintf("KUBECONFIG set to '%s' but file does not exist", [k8s.kubeconfig_path])
}

# Warn: k3d enabled but kubectl not available
warn contains msg if {
    k8s.k3d_enabled == true
    k8s.kubectl_available == false
    msg := "k3d enabled but kubectl not found in PATH"
}

# Warn: KUBECONFIG exists but kubectl cannot connect
warn contains msg if {
    k8s.kubeconfig_exists == true
    k8s.kubectl_available == true
    k8s.kubectl_connected == false
    msg := "KUBECONFIG exists but kubectl cannot connect to cluster"
}

# Info: Show kubernetes target status
info contains msg if {
    k8s.target != "none"
    msg := sprintf("Kubernetes target: %s", [k8s.target])
}

# Info: k3d stack is disabled (default)
info contains msg if {
    k8s.k3d_enabled == false
    k8s.target == "none"
    msg := "k3d stack disabled (default). Set ENABLE_K3D=true to enable local Kubernetes."
}
