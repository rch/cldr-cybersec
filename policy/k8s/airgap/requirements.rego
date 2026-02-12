# Air-Gap K8s Deployment Requirements Policy
#
# Validates requirements for Zarf-based air-gap deployment to RKE2 clusters.
# This policy ensures all prerequisites are met before attempting deployment.
#
# Run with: conftest test build/environment.json --policy policy/k8s/airgap/
#
# Config structure:
#   input.tools - Tool availability
#   input.kubernetes - Kubernetes configuration
#   input.zarf - Zarf-specific configuration

package k8s.airgap.requirements

import rego.v1

# Access sections from environment.json
tools := input.tools
k8s := input.kubernetes
zarf := input.zarf

# ==========================================================================
# Tool Requirements (DENY - blocking)
# ==========================================================================

# Deny if zarf CLI not installed
deny contains msg if {
    not tools.zarf
    msg := "zarf not installed. Install: brew install defenseunicorns/tap/zarf"
}

# Deny if kubectl not installed
deny contains msg if {
    not tools.kubectl
    msg := "kubectl not installed. Required for cluster management."
}

# Deny if helm not installed
deny contains msg if {
    not tools.helm
    msg := "helm not installed. Required for deploying Dask operator and JupyterHub."
}

# ==========================================================================
# Cluster Requirements (DENY - blocking)
# ==========================================================================

# Deny if KUBECONFIG not set or doesn't exist
deny contains msg if {
    k8s.kubeconfig_path == ""
    msg := "KUBECONFIG not set. Export KUBECONFIG to point to your RKE2 cluster config."
}

deny contains msg if {
    k8s.kubeconfig_path != ""
    not k8s.kubeconfig_exists
    msg := sprintf("KUBECONFIG file not found: %s", [k8s.kubeconfig_path])
}

# Deny if cannot connect to cluster
deny contains msg if {
    k8s.kubeconfig_exists
    tools.kubectl
    not k8s.kubectl_connected
    msg := "Cannot connect to Kubernetes cluster. Check cluster is running and network accessible."
}

# Deny if cluster is not RKE2/K3s (warn for other types)
deny contains msg if {
    k8s.kubectl_connected
    k8s.cluster_type == "none"
    msg := "Cluster type could not be determined. Zarf deployment requires RKE2 or compatible cluster."
}

# ==========================================================================
# Storage Requirements (DENY - blocking for JupyterHub)
# ==========================================================================

# Deny if no StorageClass exists (JupyterHub needs PVCs)
deny contains msg if {
    k8s.kubectl_connected
    count(k8s.storage_classes) == 0
    msg := "No StorageClass found. JupyterHub requires dynamic storage provisioning. Deploy a CSI driver or use local-path."
}

# ==========================================================================
# Registry Warnings (WARN - non-blocking)
# ==========================================================================

# Warn if external registry specified but not reachable
warn contains msg if {
    zarf.registry_url != ""
    not zarf.registry_reachable
    msg := sprintf("External registry not reachable: %s. Zarf will use internal registry.", [zarf.registry_url])
}

# Warn if no external registry and Zarf will deploy internal
warn contains msg if {
    zarf.registry_url == ""
    msg := "No external registry configured. Zarf will deploy internal registry (zarf init required)."
}

# ==========================================================================
# Ingress Warnings (WARN - non-blocking)
# ==========================================================================

# Warn if no ingress controller found
warn contains msg if {
    k8s.kubectl_connected
    count(k8s.ingress_classes) == 0
    msg := "No IngressClass found. Services will only be accessible via NodePort."
}

# Warn if ingress class is not traefik (RKE2 default)
warn contains msg if {
    k8s.kubectl_connected
    count(k8s.ingress_classes) > 0
    not "traefik" in k8s.ingress_classes
    not "nginx" in k8s.ingress_classes
    msg := sprintf("Ingress class not traefik or nginx: %v. May require manifest adjustments.", [k8s.ingress_classes])
}

# ==========================================================================
# Container Runtime Warnings (WARN - for image building)
# ==========================================================================

# Warn if no container runtime for building images
warn contains msg if {
    not tools.podman
    not tools.docker
    msg := "No container runtime (podman/docker) found. Cannot build custom images locally."
}

# ==========================================================================
# Storage Warnings (WARN - non-blocking)
# ==========================================================================

# Warn if no default StorageClass
warn contains msg if {
    k8s.kubectl_connected
    count(k8s.storage_classes) > 0
    not k8s.default_storage_class
    msg := sprintf("No default StorageClass. Manually specify in JupyterHub values. Available: %v", [k8s.storage_classes])
}

# ==========================================================================
# Info: Show configuration
# ==========================================================================

# Info: Show detected cluster type
info contains msg if {
    k8s.kubectl_connected
    k8s.cluster_type != "none"
    msg := sprintf("Cluster type: %s", [k8s.cluster_type])
}

# Info: Show kubeconfig path
info contains msg if {
    k8s.kubeconfig_exists
    msg := sprintf("KUBECONFIG: %s", [k8s.kubeconfig_path])
}

# Info: Show available tools
info contains msg if {
    tools.zarf
    msg := sprintf("zarf version: %s", [tools.zarf_version])
}

# Info: Show storage class
info contains msg if {
    k8s.kubectl_connected
    k8s.default_storage_class != ""
    msg := sprintf("Default StorageClass: %s", [k8s.default_storage_class])
}

# Info: Show ingress class
info contains msg if {
    k8s.kubectl_connected
    count(k8s.ingress_classes) > 0
    msg := sprintf("IngressClasses: %v", [k8s.ingress_classes])
}

# Info: Ready for deployment
info contains msg if {
    tools.zarf
    tools.kubectl
    tools.helm
    k8s.kubectl_connected
    count(k8s.storage_classes) > 0
    msg := "Air-gap requirements validated. Ready for Zarf deployment."
}

# ==========================================================================
# Resource Quota Warnings
# ==========================================================================

# Warn if ResourceQuotas exist that might block deployment
warn contains msg if {
    k8s.kubectl_connected
    k8s.has_resource_quotas
    msg := "ResourceQuotas detected. Ensure quotas allow Dask/JupyterHub resource requests."
}
