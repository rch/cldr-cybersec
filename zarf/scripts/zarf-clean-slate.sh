#!/bin/bash
# =============================================================================
# Zarf Clean Slate — Pre-flight cleanup for air-gap deployment
# =============================================================================
# Removes ALL Zarf and cybersec-dask resources from an RKE2 cluster to
# guarantee a known, clean state before running zarf init + deploy.
#
# Dependencies: kubectl only (no jq, no python, no zarf CLI required)
#
# ISOLATION RULE
# This script operates on whatever cluster the resolved KUBECONFIG points
# to. Multiple cybersec-dask clusters can coexist in the same AWS account
# (e.g. a us-west-1 production-like stack and a local us-east-1 dev
# stack). To prevent accidental cross-cluster cleanup, destructive runs
# require explicit context confirmation:
#
#   --i-confirm-context=<ctx>            # CLI flag
#   ZARF_CLEAN_SLATE_CONFIRM_CONTEXT=<ctx>  # env var
#
# Where <ctx> must equal `kubectl config current-context`. --dry-run and
# --verify-only are read-only and skip the check.
#
# Usage:
#   ./zarf-clean-slate.sh --i-confirm-context=<ctx>     # Full cleanup
#   ./zarf-clean-slate.sh --dry-run                     # Preview only (no changes)
#   ./zarf-clean-slate.sh --keep-provisioner --i-confirm-context=<ctx>
#   ./zarf-clean-slate.sh --clean-disk --i-confirm-context=<ctx>
#   ./zarf-clean-slate.sh --verify-only                 # Check state, no changes
# =============================================================================

set -euo pipefail

# =============================================================================
# CLI Flags
# =============================================================================
DRY_RUN=false
VERIFY_ONLY=false
KEEP_PROVISIONER=false
CLEAN_DISK=false
CONFIRM_CONTEXT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)              DRY_RUN=true;          shift ;;
        --verify-only)          VERIFY_ONLY=true;      shift ;;
        --keep-provisioner)     KEEP_PROVISIONER=true;  shift ;;
        --clean-disk)           CLEAN_DISK=true;        shift ;;
        --i-confirm-context=*)  CONFIRM_CONTEXT="${1#*=}"; shift ;;
        -h|--help)
            sed -n '2,/^# =====/{ /^# /s/^# //p }' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# =============================================================================
# Colors & Logging
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()      { echo -e "${GREEN}[ OK ]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[FAIL]${NC} $1"; }
log_dry()     { echo -e "${CYAN}[DRY]${NC}  $1"; }
log_header()  { echo -e "\n${BOLD}── $1 ──${NC}"; }

run_cmd() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_dry "$*"
    else
        "$@"
    fi
}

# =============================================================================
# Kubeconfig Resolution (RKE2 auto-detect)
# =============================================================================
resolve_kubeconfig() {
    if [[ -n "${KUBECONFIG:-}" ]] && [[ -f "$KUBECONFIG" ]]; then
        return 0
    fi
    for path in \
        "$HOME/.kube/rke2.yaml" \
        "/etc/rancher/rke2/rke2.yaml" \
        "$HOME/.kube/config"; do
        if [[ -f "$path" ]] && [[ -r "$path" ]]; then
            export KUBECONFIG="$path"
            return 0
        fi
    done
    log_error "No kubeconfig found. Set KUBECONFIG or ensure RKE2 is installed."
    exit 1
}

# =============================================================================
# Cluster context confirmation
# =============================================================================
# Print resolved kubeconfig + current-context + API server URL, then refuse
# to proceed with a destructive run unless the operator explicitly confirms
# the target context. Read-only modes (--dry-run, --verify-only) skip the
# check. See ISOLATION RULE in the header for rationale.
confirm_target_cluster() {
    local current_context server_url
    current_context=$(kubectl config current-context 2>/dev/null) || current_context="(unknown)"
    server_url=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null) || server_url="(unknown)"

    echo ""
    echo -e "${BOLD}Target cluster:${NC}"
    echo "  KUBECONFIG:      $KUBECONFIG"
    echo "  current-context: $current_context"
    echo "  api server:      $server_url"
    echo ""

    if [[ "$DRY_RUN" == "true" ]] || [[ "$VERIFY_ONLY" == "true" ]]; then
        log_info "Read-only mode — skipping context confirmation"
        return 0
    fi

    local expected="${CONFIRM_CONTEXT:-${ZARF_CLEAN_SLATE_CONFIRM_CONTEXT:-}}"
    if [[ -z "$expected" ]]; then
        log_error "Cluster context confirmation required for destructive run."
        log_error "  Re-run with: --i-confirm-context=$current_context"
        log_error "  Or:          ZARF_CLEAN_SLATE_CONFIRM_CONTEXT=$current_context $0 ..."
        exit 1
    fi
    if [[ "$expected" != "$current_context" ]]; then
        log_error "Context mismatch — refusing to operate."
        log_error "  Confirmed: $expected"
        log_error "  Current:   $current_context"
        exit 1
    fi
    log_ok "Context confirmed: $current_context"
}

# =============================================================================
# Helper: wait for namespace deletion with finalizer force-patch
# =============================================================================
wait_ns_gone() {
    local ns="$1"
    local timeout=30
    local elapsed=0

    while kubectl get ns "$ns" &>/dev/null && [[ $elapsed -lt $timeout ]]; do
        sleep 2
        elapsed=$((elapsed + 2))
    done

    # If still exists, force-patch finalizers
    if kubectl get ns "$ns" &>/dev/null; then
        log_warn "$ns stuck in Terminating — patching finalizers"
        kubectl patch ns "$ns" -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
        sleep 5
    fi

    if kubectl get ns "$ns" &>/dev/null; then
        log_warn "$ns still exists after force-patch"
        return 1
    fi
    return 0
}

# =============================================================================
# Helper: delete namespace safely
# =============================================================================
delete_ns() {
    local ns="$1"
    if ! kubectl get ns "$ns" &>/dev/null; then
        return 0
    fi
    log_info "Deleting namespace: $ns"
    if [[ "$DRY_RUN" == "true" ]]; then
        log_dry "kubectl delete namespace $ns --wait=false"
        return 0
    fi
    kubectl delete namespace "$ns" --wait=false 2>/dev/null || true
    wait_ns_gone "$ns"
}

# =============================================================================
# Phase 0: Discovery
# =============================================================================
discover() {
    log_header "Phase 0: Discovery"

    local found=false

    # Namespaces
    local app_namespaces="dask dask-operator jupyterhub panel-viz"
    local infra_namespaces="zarf local-path-storage"

    echo ""
    echo "  Namespaces:"
    for ns in $app_namespaces $infra_namespaces; do
        if kubectl get ns "$ns" &>/dev/null; then
            local phase
            phase=$(kubectl get ns "$ns" -o jsonpath='{.status.phase}' 2>/dev/null) || phase="unknown"
            echo "    $ns ($phase)"
            found=true
        fi
    done
    $found || echo "    (none)"

    # Cluster-scoped resources
    found=false
    echo ""
    echo "  Cluster-scoped resources:"

    # Webhooks
    for wh in $(kubectl get mutatingwebhookconfigurations -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$wh" in *zarf*)
            echo "    MutatingWebhook: $wh"
            found=true
            ;;
        esac
    done
    for wh in $(kubectl get validatingwebhookconfigurations -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$wh" in *zarf*)
            echo "    ValidatingWebhook: $wh"
            found=true
            ;;
        esac
    done

    # CRDs
    if kubectl get crd daskclusters.kubernetes.dask.org &>/dev/null; then
        echo "    CRD: daskclusters.kubernetes.dask.org"
        found=true
    fi

    # ClusterRoles
    for cr in $(kubectl get clusterroles -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$cr" in *zarf*|*dask-operator*)
            echo "    ClusterRole: $cr"
            found=true
            ;;
        esac
    done

    # ClusterRoleBindings
    for crb in $(kubectl get clusterrolebindings -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$crb" in *zarf*|*dask-operator*)
            echo "    ClusterRoleBinding: $crb"
            found=true
            ;;
        esac
    done

    $found || echo "    (none)"

    # PVs
    found=false
    echo ""
    echo "  Persistent Volumes:"
    for pv in $(kubectl get pv -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$pv" in *zarf*)
            local pv_phase
            pv_phase=$(kubectl get pv "$pv" -o jsonpath='{.status.phase}' 2>/dev/null) || pv_phase="unknown"
            echo "    $pv ($pv_phase)"
            found=true
            ;;
        esac
    done
    $found || echo "    (none)"

    # StorageClass
    echo ""
    echo "  StorageClass:"
    if kubectl get storageclass local-path &>/dev/null; then
        echo "    local-path (exists)"
    else
        echo "    local-path (not found)"
    fi

    echo ""
}

# =============================================================================
# Phase 1: CRD Instances (must delete before namespaces)
# =============================================================================
phase_1_crd_instances() {
    log_header "Phase 1: CRD Instances"

    if kubectl get crd daskclusters.kubernetes.dask.org &>/dev/null; then
        local clusters
        clusters=$(kubectl get daskclusters -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>/dev/null) || true

        if [[ -n "$clusters" ]]; then
            while IFS='/' read -r ns name; do
                [[ -z "$name" ]] && continue
                log_info "Deleting DaskCluster $ns/$name"
                if [[ "$DRY_RUN" == "true" ]]; then
                    log_dry "kubectl delete daskcluster $name -n $ns"
                else
                    # Patch finalizers first to prevent hang
                    kubectl patch daskcluster "$name" -n "$ns" \
                        -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
                    kubectl delete daskcluster "$name" -n "$ns" \
                        --timeout=15s 2>/dev/null || true
                fi
            done <<< "$clusters"
        else
            log_ok "No DaskCluster instances found"
        fi
    else
        log_ok "DaskCluster CRD not installed"
    fi
}

# =============================================================================
# Phase 2: Application Namespaces
# =============================================================================
phase_2_app_namespaces() {
    log_header "Phase 2: Application Namespaces"

    for ns in dask dask-operator jupyterhub panel-viz; do
        delete_ns "$ns"
    done

    # Verify
    local remaining=""
    for ns in dask dask-operator jupyterhub panel-viz; do
        if kubectl get ns "$ns" &>/dev/null; then
            remaining="$remaining $ns"
        fi
    done

    if [[ -z "$remaining" ]]; then
        log_ok "All application namespaces removed"
    else
        log_warn "Namespaces still present:$remaining"
    fi
}

# =============================================================================
# Phase 3: Zarf Namespace & Init Resources
# =============================================================================
phase_3_zarf_namespace() {
    log_header "Phase 3: Zarf Namespace"

    if ! kubectl get ns zarf &>/dev/null; then
        log_ok "Zarf namespace not present"
        return 0
    fi

    # Delete PVCs first (before namespace) to avoid stuck finalizers
    local pvcs
    pvcs=$(kubectl get pvc -n zarf -o jsonpath='{.items[*].metadata.name}' 2>/dev/null) || true
    for pvc in $pvcs; do
        log_info "Deleting PVC zarf/$pvc"
        if [[ "$DRY_RUN" != "true" ]]; then
            kubectl patch pvc "$pvc" -n zarf \
                -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
            kubectl delete pvc "$pvc" -n zarf \
                --force --grace-period=0 2>/dev/null || true
        else
            log_dry "kubectl delete pvc $pvc -n zarf"
        fi
    done

    delete_ns "zarf"
}

# =============================================================================
# Phase 4: Cluster-Scoped Resources
# =============================================================================
phase_4_cluster_scoped() {
    log_header "Phase 4: Cluster-Scoped Resources"

    # Mutating webhooks
    for wh in $(kubectl get mutatingwebhookconfigurations -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$wh" in *zarf*)
            log_info "Deleting MutatingWebhookConfiguration: $wh"
            run_cmd kubectl delete mutatingwebhookconfigurations "$wh" 2>/dev/null || true
            ;;
        esac
    done

    # Validating webhooks
    for wh in $(kubectl get validatingwebhookconfigurations -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$wh" in *zarf*)
            log_info "Deleting ValidatingWebhookConfiguration: $wh"
            run_cmd kubectl delete validatingwebhookconfigurations "$wh" 2>/dev/null || true
            ;;
        esac
    done

    # ClusterRoleBindings (before ClusterRoles)
    for crb in $(kubectl get clusterrolebindings -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$crb" in *zarf*|*dask-operator*)
            log_info "Deleting ClusterRoleBinding: $crb"
            run_cmd kubectl delete clusterrolebinding "$crb" 2>/dev/null || true
            ;;
        esac
    done

    # ClusterRoles
    for cr in $(kubectl get clusterroles -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$cr" in *zarf*|*dask-operator*)
            log_info "Deleting ClusterRole: $cr"
            run_cmd kubectl delete clusterrole "$cr" 2>/dev/null || true
            ;;
        esac
    done

    # DaskCluster CRD
    if kubectl get crd daskclusters.kubernetes.dask.org &>/dev/null; then
        log_info "Deleting CRD: daskclusters.kubernetes.dask.org"
        run_cmd kubectl delete crd daskclusters.kubernetes.dask.org 2>/dev/null || true
    fi

    log_ok "Cluster-scoped resources cleaned"
}

# =============================================================================
# Phase 5: Persistent Volumes
# =============================================================================
phase_5_storage() {
    log_header "Phase 5: Storage"

    # PVs with zarf in the name
    for pv in $(kubectl get pv -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$pv" in *zarf*)
            log_info "Deleting PV: $pv"
            if [[ "$DRY_RUN" != "true" ]]; then
                kubectl patch pv "$pv" \
                    -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
                kubectl delete pv "$pv" --force --grace-period=0 2>/dev/null || true
            else
                log_dry "kubectl delete pv $pv"
            fi
            ;;
        esac
    done

    # local-path-provisioner
    if [[ "$KEEP_PROVISIONER" == "true" ]]; then
        log_info "Keeping local-path-provisioner (--keep-provisioner)"
    else
        if kubectl get ns local-path-storage &>/dev/null; then
            delete_ns "local-path-storage"
        fi
        if kubectl get storageclass local-path &>/dev/null; then
            log_info "Deleting StorageClass: local-path"
            run_cmd kubectl delete storageclass local-path 2>/dev/null || true
        fi
    fi

    # Host disk cleanup
    if [[ "$CLEAN_DISK" == "true" ]]; then
        local reg_dir="/var/lib/zarf-registry"
        if [[ -d "$reg_dir" ]]; then
            log_info "Wiping $reg_dir"
            if [[ "$DRY_RUN" != "true" ]]; then
                sudo rm -rf "$reg_dir"
                log_ok "Registry data wiped"
            else
                log_dry "sudo rm -rf $reg_dir"
            fi
        fi
    fi

    log_ok "Storage cleaned"
}

# =============================================================================
# Phase 6: Verification
# =============================================================================
verify() {
    log_header "Verification"

    local issues=0

    # Namespaces
    local check_namespaces="zarf dask dask-operator jupyterhub panel-viz"
    if [[ "$KEEP_PROVISIONER" != "true" ]]; then
        check_namespaces="$check_namespaces local-path-storage"
    fi

    for ns in $check_namespaces; do
        if kubectl get ns "$ns" &>/dev/null; then
            log_error "Namespace still exists: $ns"
            issues=$((issues + 1))
        fi
    done

    # Webhooks
    for wh in $(kubectl get mutatingwebhookconfigurations -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$wh" in *zarf*)
            log_error "Webhook still exists: $wh"
            issues=$((issues + 1))
            ;;
        esac
    done

    # CRDs
    if kubectl get crd daskclusters.kubernetes.dask.org &>/dev/null; then
        log_error "CRD still exists: daskclusters.kubernetes.dask.org"
        issues=$((issues + 1))
    fi

    # PVs
    for pv in $(kubectl get pv -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        case "$pv" in *zarf*)
            log_error "PV still exists: $pv"
            issues=$((issues + 1))
            ;;
        esac
    done

    # StorageClass status
    echo ""
    if kubectl get storageclass local-path &>/dev/null; then
        log_ok "StorageClass local-path: available"
    elif [[ "$KEEP_PROVISIONER" == "true" ]]; then
        log_warn "StorageClass local-path: not found (was --keep-provisioner used on first run?)"
    else
        log_info "StorageClass local-path: not present (apply local-path-provisioner.yaml before zarf init)"
    fi

    # Cluster nodes
    echo ""
    local node_count
    node_count=$(kubectl get nodes --no-headers 2>/dev/null | wc -l) || node_count=0
    local ready_count
    ready_count=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || true)
    log_info "Cluster: $ready_count/$node_count nodes Ready"

    echo ""
    if [[ $issues -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}CLEAN SLATE${NC} — ready for zarf init"
    else
        echo -e "${RED}${BOLD}$issues issue(s) remaining${NC} — re-run or clean manually"
        return 1
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo -e "${BOLD}Zarf Clean Slate — Pre-flight Cleanup${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${CYAN}(dry-run mode — no changes will be made)${NC}"
    fi
    echo ""

    resolve_kubeconfig
    log_info "Using KUBECONFIG=$KUBECONFIG"

    # Verify cluster connectivity
    if ! kubectl cluster-info &>/dev/null; then
        log_error "Cannot connect to cluster. Check KUBECONFIG and network."
        exit 1
    fi

    confirm_target_cluster

    discover

    if [[ "$VERIFY_ONLY" == "true" ]]; then
        verify
        exit $?
    fi

    phase_1_crd_instances
    phase_2_app_namespaces
    phase_3_zarf_namespace
    phase_4_cluster_scoped
    phase_5_storage
    verify
}

main "$@"
