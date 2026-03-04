#!/bin/bash
# =============================================================================
# Zarf Init Recovery Script — v1.2.1
# =============================================================================
# Lightweight recovery tool for failed `zarf init` attempts. Cleans up stale
# PVC/PV state, recreates the registry PV with proper claimRef pre-binding,
# and optionally retries `zarf init`.
#
# This script does NOT build images or deploy the cybersec-dask package —
# use verify-zarf-deployment.sh for full deployment.
#
# Usage:
#   ./zarf-init-recovery.sh                # Recover + retry init
#   ./zarf-init-recovery.sh --dry-run      # Preview recovery steps
#   ./zarf-init-recovery.sh --verify-only  # Read-only state check
#   ./zarf-init-recovery.sh --skip-init    # Clean up only, don't retry init
#
# Requirements:
#   - kubectl available and KUBECONFIG resolvable
#   - Zarf CLI available (unless --skip-init or --verify-only)
# =============================================================================

set -euo pipefail

# =============================================================================
# Paths & Constants
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZARF_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$ZARF_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
REGISTRY_STORAGE_PATH="${REGISTRY_STORAGE_PATH:-/var/lib/zarf-registry}"
REGISTRY_PVC_SIZE="${REGISTRY_PVC_SIZE:-5Gi}"

# =============================================================================
# CLI Flags
# =============================================================================
DRY_RUN=false
VERIFY_ONLY=false
SKIP_INIT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)      DRY_RUN=true;      shift ;;
        --verify-only)  VERIFY_ONLY=true;   shift ;;
        --skip-init)    SKIP_INIT=true;     shift ;;
        -h|--help)
            cat <<'USAGE'
Usage: zarf-init-recovery.sh [OPTIONS]

Recover from a failed `zarf init` by cleaning stale PVC/PV state and retrying.

Options:
  --dry-run        Show what would be done without executing
  --verify-only    Read-only state check (no changes)
  --skip-init      Clean up stale state only, don't retry zarf init

Environment Variables:
  KUBECONFIG              Path to kubeconfig (auto-detected if unset)
  REGISTRY_STORAGE_PATH   Host path for registry data (default: /var/lib/zarf-registry)
  REGISTRY_PVC_SIZE       Registry PV size (default: 5Gi)
USAGE
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# =============================================================================
# Logging (same signatures as verify-zarf-deployment.sh)
# =============================================================================
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()    { echo -e "\n${CYAN}${BOLD}=== $1 ===${NC}"; }

# =============================================================================
# Source .env (if present)
# =============================================================================
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
fi

# =============================================================================
# Kubeconfig Resolution (5-tier fallback, from verify-zarf-deployment.sh)
# =============================================================================
_resolve_kubeconfig() {
    if [[ -n "${KUBECONFIG:-}" ]] && [[ -f "$KUBECONFIG" ]] && [[ -r "$KUBECONFIG" ]]; then
        log_info "Using KUBECONFIG=$KUBECONFIG"
        return 0
    fi
    if [[ -f "$HOME/.kube/rke2.yaml" ]] && [[ -r "$HOME/.kube/rke2.yaml" ]]; then
        KUBECONFIG="$HOME/.kube/rke2.yaml"; export KUBECONFIG
        log_info "Using user kubeconfig: $KUBECONFIG"; return 0
    fi
    if [[ "${CYBERSEC_K8S_TARGET:-}" == "k3d" ]]; then
        KUBECONFIG="${DEVENV_STATE:-.devenv/state}/kubeconfig"; export KUBECONFIG
        log_info "Using k3d kubeconfig: $KUBECONFIG"; return 0
    fi
    if [[ -f "/etc/rancher/rke2/rke2.yaml" ]]; then
        if [[ -r "/etc/rancher/rke2/rke2.yaml" ]]; then
            KUBECONFIG="/etc/rancher/rke2/rke2.yaml"; export KUBECONFIG
            log_info "Using RKE2 kubeconfig: $KUBECONFIG"; return 0
        else
            log_error "RKE2 kubeconfig not readable: /etc/rancher/rke2/rke2.yaml"
            echo "  Fix: sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/rke2.yaml"
            echo "       sudo chown \$(id -u):\$(id -g) ~/.kube/rke2.yaml"
            return 1
        fi
    fi
    if [[ -f "$HOME/.kube/config" ]] && [[ -r "$HOME/.kube/config" ]]; then
        KUBECONFIG="$HOME/.kube/config"; export KUBECONFIG
        log_info "Using default kubeconfig: $KUBECONFIG"; return 0
    fi
    log_error "No kubeconfig found. Set KUBECONFIG or install a local cluster."
    return 1
}

# =============================================================================
# detect_state — query PVC/PV/namespace and print summary
# =============================================================================
detect_state() {
    log_step "Detecting Zarf Registry State"

    NS_EXISTS=false
    PVC_EXISTS=false
    PVC_PHASE=""
    PVC_VOLUME_NAME=""
    PVC_HAS_FINALIZERS=false
    PV_EXISTS=false
    PV_PHASE=""
    PV_HAS_CLAIM_REF=false
    PV_HAS_FINALIZERS=false
    REGISTRY_RUNNING=false

    # Namespace
    if kubectl get ns zarf &>/dev/null; then
        NS_EXISTS=true
    fi

    # PVC
    local pvc_json
    pvc_json=$(kubectl get pvc zarf-docker-registry -n zarf -o json 2>/dev/null) || true
    if [[ -n "$pvc_json" ]]; then
        PVC_EXISTS=true
        PVC_PHASE=$(echo "$pvc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('phase',''))" 2>/dev/null) || true
        PVC_VOLUME_NAME=$(echo "$pvc_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('spec',{}).get('volumeName',''))" 2>/dev/null) || true
        local finalizers
        finalizers=$(echo "$pvc_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('metadata',{}).get('finalizers',[])))" 2>/dev/null) || finalizers=0
        if [[ "$finalizers" -gt 0 ]]; then
            PVC_HAS_FINALIZERS=true
        fi
    fi

    # PV
    local pv_json
    pv_json=$(kubectl get pv zarf-registry-pv -o json 2>/dev/null) || true
    if [[ -n "$pv_json" ]]; then
        PV_EXISTS=true
        PV_PHASE=$(echo "$pv_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('phase',''))" 2>/dev/null) || true
        local claim_ref
        claim_ref=$(echo "$pv_json" | python3 -c "import sys,json; cr=json.load(sys.stdin).get('spec',{}).get('claimRef',{}); print(cr.get('name',''))" 2>/dev/null) || true
        if [[ -n "$claim_ref" ]]; then
            PV_HAS_CLAIM_REF=true
        fi
        local pv_finalizers
        pv_finalizers=$(echo "$pv_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('metadata',{}).get('finalizers',[])))" 2>/dev/null) || pv_finalizers=0
        if [[ "$pv_finalizers" -gt 0 ]]; then
            PV_HAS_FINALIZERS=true
        fi
    fi

    # Registry pod
    local reg_phase
    reg_phase=$(kubectl get pods -n zarf -l app=docker-registry \
        -o jsonpath='{.items[0].status.phase}' 2>/dev/null) || true
    if [[ "$reg_phase" == "Running" ]]; then
        REGISTRY_RUNNING=true
    fi

    # Print summary table
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  Zarf Registry State                                 ║${NC}"
    echo -e "${BOLD}╠══════════════════════════════════════════════════════╣${NC}"
    printf "${BOLD}║${NC}  %-24s %s\n" "Namespace zarf:" "$(if $NS_EXISTS; then echo 'exists'; else echo 'not found'; fi)"
    printf "${BOLD}║${NC}  %-24s %s\n" "PVC:" "$(if $PVC_EXISTS; then echo "${PVC_PHASE:-unknown}"; else echo 'not found'; fi)"
    if $PVC_EXISTS; then
        printf "${BOLD}║${NC}  %-24s %s\n" "  volumeName:" "${PVC_VOLUME_NAME:-<empty>}"
        printf "${BOLD}║${NC}  %-24s %s\n" "  finalizers:" "$(if $PVC_HAS_FINALIZERS; then echo 'YES (stuck)'; else echo 'none'; fi)"
    fi
    printf "${BOLD}║${NC}  %-24s %s\n" "PV:" "$(if $PV_EXISTS; then echo "${PV_PHASE:-unknown}"; else echo 'not found'; fi)"
    if $PV_EXISTS; then
        printf "${BOLD}║${NC}  %-24s %s\n" "  claimRef:" "$(if $PV_HAS_CLAIM_REF; then echo 'set'; else echo 'MISSING'; fi)"
        printf "${BOLD}║${NC}  %-24s %s\n" "  finalizers:" "$(if $PV_HAS_FINALIZERS; then echo 'YES (stuck)'; else echo 'none'; fi)"
    fi
    printf "${BOLD}║${NC}  %-24s %s\n" "Registry pod:" "$(if $REGISTRY_RUNNING; then echo 'Running'; else echo 'not running'; fi)"
    echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Assess whether recovery is needed
    if $REGISTRY_RUNNING && [[ "$PVC_PHASE" == "Bound" ]]; then
        log_success "Registry is healthy — no recovery needed"
        return 0
    fi

    if [[ "$PVC_PHASE" == "Lost" ]]; then
        log_warn "PVC in Lost phase — recovery required"
    elif [[ "$PVC_PHASE" == "Pending" ]]; then
        log_warn "PVC in Pending phase — may need PV recreation"
    elif $PV_EXISTS && [[ "$PV_HAS_CLAIM_REF" == "false" ]]; then
        log_warn "PV exists without claimRef — won't auto-bind to PVC"
    elif $PVC_HAS_FINALIZERS || $PV_HAS_FINALIZERS; then
        log_warn "Stuck finalizers detected — cleanup required"
    elif ! $NS_EXISTS; then
        log_info "Zarf not initialized — ready for fresh init"
    fi

    return 0
}

# =============================================================================
# cleanup_stale_state — remove stuck PVC/PV/namespace
# =============================================================================
cleanup_stale_state() {
    log_step "Cleaning Stale State"

    local cleaned=false

    # PVC cleanup
    if $PVC_EXISTS && [[ "$PVC_PHASE" != "Bound" || "$PVC_HAS_FINALIZERS" == "true" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY-RUN] Would patch PVC finalizers and force-delete"
        else
            log_info "Removing PVC zarf-docker-registry..."
            kubectl patch pvc zarf-docker-registry -n zarf \
                -p '{"metadata":{"finalizers":null}}' 2>/dev/null || true
            kubectl delete pvc zarf-docker-registry -n zarf \
                --force --grace-period=0 2>/dev/null || true
            log_success "PVC removed"
            cleaned=true
        fi
    fi

    # PV cleanup
    if $PV_EXISTS && [[ "$PV_PHASE" != "Available" && "$PV_PHASE" != "Bound" ]] || \
       { $PV_EXISTS && [[ "$PV_HAS_CLAIM_REF" == "false" ]]; }; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY-RUN] Would patch PV finalizers and force-delete"
        else
            log_info "Removing PV zarf-registry-pv..."
            kubectl patch pv zarf-registry-pv \
                -p '{"metadata":{"finalizers":null}}' 2>/dev/null || true
            kubectl delete pv zarf-registry-pv \
                --force --grace-period=0 2>/dev/null || true
            log_success "PV removed"
            cleaned=true
        fi
    fi

    # Namespace cleanup (only if PVC was Lost or namespace has no running pods)
    if $NS_EXISTS && [[ "$PVC_PHASE" == "Lost" ]] && ! $REGISTRY_RUNNING; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY-RUN] Would delete and finalize zarf namespace"
        else
            log_info "Removing stale zarf namespace..."
            kubectl delete namespace zarf --wait=false 2>/dev/null || true
            kubectl patch namespace zarf -p '{"metadata":{"finalizers":null}}' 2>/dev/null || true
            # Wait for namespace deletion
            local waited=0
            while kubectl get namespace zarf &>/dev/null && [[ $waited -lt 30 ]]; do
                sleep 2
                ((waited+=2))
            done
            if kubectl get namespace zarf &>/dev/null; then
                log_warn "Namespace still exists after 30s — may need manual cleanup"
            else
                log_success "Namespace removed"
            fi
            cleaned=true
        fi
    fi

    if [[ "$cleaned" == "false" ]] && [[ "$DRY_RUN" == "false" ]]; then
        log_info "No stale state to clean"
    fi
}

# =============================================================================
# recreate_pv — create PV with claimRef pre-binding
# =============================================================================
recreate_pv() {
    log_step "Creating Registry PV"

    # Skip if healthy PV already exists
    local current_phase
    current_phase=$(kubectl get pv zarf-registry-pv \
        -o jsonpath='{.status.phase}' 2>/dev/null) || true
    if [[ "$current_phase" == "Available" || "$current_phase" == "Bound" ]]; then
        log_success "PV zarf-registry-pv already healthy (phase: $current_phase)"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create storage dir $REGISTRY_STORAGE_PATH"
        log_info "[DRY-RUN] Would create PV ($REGISTRY_PVC_SIZE) with claimRef"
        return 0
    fi

    # Create storage directory
    log_info "Creating storage directory: $REGISTRY_STORAGE_PATH"
    sudo mkdir -p "$REGISTRY_STORAGE_PATH"
    sudo chown 1000:2000 "$REGISTRY_STORAGE_PATH"
    sudo chmod 777 "$REGISTRY_STORAGE_PATH"
    sudo chcon -R -t container_file_t "$REGISTRY_STORAGE_PATH" 2>/dev/null || true

    # Create PV with claimRef
    log_info "Creating PersistentVolume ($REGISTRY_PVC_SIZE)..."
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: $REGISTRY_PVC_SIZE
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: $REGISTRY_STORAGE_PATH
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF
    log_success "Created PV ($REGISTRY_PVC_SIZE) with claimRef pre-binding"
}

# =============================================================================
# retry_init — run zarf init
# =============================================================================
retry_init() {
    log_step "Retrying Zarf Init"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would run: zarf init --confirm --set REGISTRY_PVC_SIZE=$REGISTRY_PVC_SIZE"
        return 0
    fi

    if ! command -v zarf &>/dev/null; then
        log_error "Zarf CLI not found in PATH"
        return 1
    fi

    cd "$ZARF_DIR"
    log_info "Running zarf init..."
    if zarf init --confirm --set REGISTRY_PVC_SIZE="$REGISTRY_PVC_SIZE" 2>&1 \
        | tee /tmp/zarf-init-recovery.log | tail -30; then
        log_success "Zarf initialized successfully"
    else
        log_error "Zarf init failed — see /tmp/zarf-init-recovery.log"
        tail -50 /tmp/zarf-init-recovery.log
        return 1
    fi
}

# =============================================================================
# verify_registry — confirm registry is running and PVC is bound
# =============================================================================
verify_registry() {
    log_step "Verifying Registry"

    local max_wait=60
    local waited=0

    while [[ $waited -lt $max_wait ]]; do
        local phase
        phase=$(kubectl get pods -n zarf -l app=docker-registry \
            -o jsonpath='{.items[0].status.phase}' 2>/dev/null) || true
        if [[ "$phase" == "Running" ]]; then
            log_success "Registry pod: Running"
            break
        fi
        log_info "Waiting for registry pod... ($waited/${max_wait}s, phase: ${phase:-pending})"
        sleep 5
        ((waited+=5))
    done

    local pvc_phase
    pvc_phase=$(kubectl get pvc zarf-docker-registry -n zarf \
        -o jsonpath='{.status.phase}' 2>/dev/null) || true
    if [[ "$pvc_phase" == "Bound" ]]; then
        log_success "Registry PVC: Bound"
    else
        log_warn "Registry PVC: ${pvc_phase:-not found}"
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo -e "${BOLD}Zarf Init Recovery — v1.2.1${NC}"
    echo ""

    if ! _resolve_kubeconfig; then
        exit 1
    fi

    # Always detect state
    detect_state

    # Verify-only: stop after state detection
    if [[ "$VERIFY_ONLY" == "true" ]]; then
        exit 0
    fi

    # If registry is already healthy, nothing to do
    if $REGISTRY_RUNNING && [[ "$PVC_PHASE" == "Bound" ]]; then
        log_info "Use --verify-only to just check state, or run verify-zarf-deployment.sh for full verification."
        exit 0
    fi

    # Recovery flow
    cleanup_stale_state
    recreate_pv

    if [[ "$SKIP_INIT" == "true" ]]; then
        log_info "Skipping zarf init (--skip-init). Run manually:"
        echo "  zarf init --confirm --set REGISTRY_PVC_SIZE=$REGISTRY_PVC_SIZE"
        exit 0
    fi

    retry_init
    verify_registry

    echo ""
    log_success "Recovery complete. Next steps:"
    echo "  - Deploy: zarf package deploy zarf-package-cybersec-dask-amd64-*.tar.zst --confirm"
    echo "  - Verify: ./scripts/verify-zarf-deployment.sh --verify-only"
}

main "$@"
