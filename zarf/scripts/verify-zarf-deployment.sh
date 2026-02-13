#!/bin/bash
# =============================================================================
# Zarf Deployment Verification Script
# =============================================================================
# This script verifies and deploys the cybersec-dask Zarf package to a local
# RKE2 cluster. It supports two modes of operation:
#
# BUILD MODE (connected environment):
#   ./verify-zarf-deployment.sh
#   - Builds custom images (requires network for base images)
#   - Creates Zarf package
#   - Deploys to cluster
#
# DEPLOY MODE (air-gap environment):
#   ./verify-zarf-deployment.sh --skip-build
#   - Uses pre-built Zarf package and init package
#   - Deploys to cluster without network access
#   - Requires: zarf-init-amd64-*.tar.zst, zarf-package-cybersec-dask-*.tar.zst
#
# Requirements:
#   - RKE2 installed and running (kube-system pods provide Zarf injector bootstrap)
#   - Zarf CLI available
#   - Podman for building custom images (BUILD MODE only)
#   - Pre-built Zarf packages (DEPLOY MODE only)
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZARF_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$ZARF_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Flags
SKIP_INIT=false
SKIP_BUILD=false
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-init) SKIP_INIT=true; shift ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--skip-init] [--skip-build] [--dry-run]"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

# =============================================================================
# Step 1: Check Prerequisites
# =============================================================================
check_prerequisites() {
    log_step "Step 1: Checking Prerequisites"

    local errors=0

    # Check RKE2 service
    if systemctl is-active --quiet rke2-server 2>/dev/null; then
        log_success "RKE2 server is running"
    else
        log_error "RKE2 server is not running"
        log_info "Start with: sudo systemctl start rke2-server"
        ((errors++))
    fi

    # Check for kubeconfig
    if [[ -f /etc/rancher/rke2/rke2.yaml ]]; then
        log_success "RKE2 kubeconfig exists"
        export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
    else
        log_error "RKE2 kubeconfig not found at /etc/rancher/rke2/rke2.yaml"
        ((errors++))
    fi

    # Check kubectl access (using RKE2's kubectl)
    local KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
    if [[ -x "$KUBECTL" ]]; then
        if sudo $KUBECTL --kubeconfig=/etc/rancher/rke2/rke2.yaml get nodes &>/dev/null; then
            log_success "kubectl can connect to cluster"
            # Show node status
            sudo $KUBECTL --kubeconfig=/etc/rancher/rke2/rke2.yaml get nodes -o wide
        else
            log_error "kubectl cannot connect to cluster"
            ((errors++))
        fi
    else
        log_error "RKE2 kubectl not found"
        ((errors++))
    fi

    # Check Zarf CLI
    if command -v zarf &>/dev/null; then
        log_success "Zarf CLI found: $(zarf version 2>/dev/null || echo 'unknown version')"
    else
        log_error "Zarf CLI not found"
        ((errors++))
    fi

    # Check Podman (for building images)
    if command -v podman &>/dev/null; then
        log_success "Podman found: $(podman --version)"
    else
        log_warn "Podman not found - custom image builds will fail"
    fi

    # Check if zarf.yaml exists
    if [[ -f "$ZARF_DIR/zarf.yaml" ]]; then
        log_success "zarf.yaml found at $ZARF_DIR/zarf.yaml"
    else
        log_error "zarf.yaml not found"
        ((errors++))
    fi

    if [[ $errors -gt 0 ]]; then
        log_error "Prerequisites check failed with $errors error(s)"
        return 1
    fi

    log_success "All prerequisites passed"
    return 0
}

# =============================================================================
# Step 2: Verify Injector Prerequisites (kube-system pods)
# =============================================================================
verify_injector_prerequisites() {
    log_step "Step 2: Verifying Injector Prerequisites"

    # Zarf's injector needs a RUNNING POD with a suitable image to bootstrap.
    # In a true air-gap environment, we CANNOT pull from docker.io.
    # RKE2's kube-system pods (coredns, metrics-server) already satisfy this requirement.

    local KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
    local KUBECONFIG="/etc/rancher/rke2/rke2.yaml"

    log_info "Checking for running kube-system pods (required for Zarf injector bootstrap)..."

    # Wait for kube-system pods to be ready
    local max_wait=120
    local waited=0
    local running_pods=0

    while [[ $waited -lt $max_wait ]]; do
        running_pods=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n kube-system --no-headers 2>/dev/null | grep -c "Running" || echo "0")
        if [[ "$running_pods" -ge 2 ]]; then
            log_success "Found $running_pods running pods in kube-system"
            break
        fi
        log_info "Waiting for kube-system pods... ($waited/$max_wait seconds)"
        sleep 5
        ((waited+=5))
    done

    if [[ "$running_pods" -lt 2 ]]; then
        log_error "Insufficient running pods in kube-system (found: $running_pods, need: 2+)"
        log_error "Zarf injector requires running pods to bootstrap"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n kube-system
        return 1
    fi

    # Show kube-system pods that Zarf can use for injection
    log_info "kube-system pods available for Zarf injector:"
    sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n kube-system

    # Verify specific pods Zarf typically uses
    local coredns_running=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n kube-system -l k8s-app=kube-dns --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    if [[ "$coredns_running" -ge 1 ]]; then
        log_success "CoreDNS pod(s) running - suitable for Zarf injector"
    fi

    local metrics_running
    metrics_running=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n kube-system -l k8s-app=metrics-server --no-headers 2>/dev/null | grep -c "Running") || metrics_running=0
    if [[ "$metrics_running" -ge 1 ]]; then
        log_success "Metrics-server pod(s) running - suitable for Zarf injector"
    fi

    return 0
}

# =============================================================================
# Step 3: Start Local Registry (BUILD PHASE ONLY - requires network)
# =============================================================================
start_local_registry() {
    log_step "Step 3: Starting Local Registry (for image builds)"

    # NOTE: This step is for the BUILD phase only.
    # In a true air-gap deployment, images are already packaged in the Zarf archive.
    # Use --skip-build to skip this step when deploying pre-built packages.

    if [[ "$SKIP_BUILD" == "true" ]]; then
        log_info "Skipping local registry (--skip-build mode)"
        return 0
    fi

    if ! command -v podman &>/dev/null; then
        log_warn "Podman not available, skipping local registry"
        return 0
    fi

    # Check if registry is already running
    if podman ps --format '{{.Names}}' | grep -q '^registry$'; then
        log_success "Local registry already running"
        return 0
    fi

    # Check if registry container exists but is stopped
    if podman ps -a --format '{{.Names}}' | grep -q '^registry$'; then
        log_info "Starting existing registry container..."
        podman start registry
    else
        log_info "Creating new registry container..."
        # NOTE: This requires network access - for BUILD phase only
        podman run -d --name registry -p 5555:5000 registry:2
    fi

    # Wait for registry to be ready
    sleep 2
    if curl -s http://localhost:5555/v2/ &>/dev/null; then
        log_success "Local registry is running on port 5555"
    else
        log_error "Local registry failed to start"
        return 1
    fi

    return 0
}

# =============================================================================
# Step 4: Build Custom Dask Image
# =============================================================================
build_custom_image() {
    log_step "Step 4: Building Custom Dask Image"

    if [[ "$SKIP_BUILD" == "true" ]]; then
        log_info "Skipping image build (--skip-build)"
        return 0
    fi

    if ! command -v podman &>/dev/null; then
        log_error "Podman required for building custom images"
        return 1
    fi

    local DOCKERFILE="$ZARF_DIR/images/Dockerfile.cybersec-dask"
    local IMAGE_TAG="localhost:5555/cybersec-dask:2024.8.0"

    if [[ ! -f "$DOCKERFILE" ]]; then
        log_error "Dockerfile not found: $DOCKERFILE"
        return 1
    fi

    log_info "Building image: $IMAGE_TAG"
    cd "$ZARF_DIR/images"

    if podman build -t "$IMAGE_TAG" -f Dockerfile.cybersec-dask . 2>&1 | tail -10; then
        log_success "Image built: $IMAGE_TAG"
    else
        log_error "Image build failed"
        return 1
    fi

    log_info "Pushing image to local registry..."
    if podman push --tls-verify=false "$IMAGE_TAG" 2>&1 | tail -5; then
        log_success "Image pushed to registry"
    else
        log_error "Image push failed"
        return 1
    fi

    cd "$ZARF_DIR"
    return 0
}

# =============================================================================
# Step 5: Download Zarf Init Package
# =============================================================================
download_zarf_init() {
    log_step "Step 5: Downloading Zarf Init Package"

    local INIT_PACKAGE="$ZARF_DIR/zarf-init-amd64-v$(zarf version 2>/dev/null | tr -d 'v').tar.zst"

    # Check for any init package
    if ls "$ZARF_DIR"/zarf-init-amd64-*.tar.zst &>/dev/null; then
        log_success "Zarf init package already exists"
        ls -la "$ZARF_DIR"/zarf-init-amd64-*.tar.zst
        return 0
    fi

    log_info "Downloading Zarf init package..."
    cd "$ZARF_DIR"
    if zarf tools download-init 2>&1 | tail -10; then
        log_success "Zarf init package downloaded"
    else
        log_error "Failed to download Zarf init package"
        return 1
    fi

    return 0
}

# =============================================================================
# Step 5.5: Setup Storage for Zarf Registry
# =============================================================================
setup_storage() {
    log_step "Step 5.5: Setting Up Storage for Zarf Registry"

    local KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
    local KUBECONFIG="/etc/rancher/rke2/rke2.yaml"
    local STORAGE_PATH="/var/lib/zarf-registry"

    # Create storage directory with proper permissions
    log_info "Creating storage directory: $STORAGE_PATH"
    sudo mkdir -p "$STORAGE_PATH"
    sudo chmod 777 "$STORAGE_PATH"

    # Check if PV already exists
    if sudo $KUBECTL --kubeconfig=$KUBECONFIG get pv zarf-registry-pv &>/dev/null; then
        log_success "PV zarf-registry-pv already exists"
        return 0
    fi

    # Create PV for Zarf registry (RKE2 doesn't have a default storage class)
    log_info "Creating PersistentVolume for Zarf registry..."
    cat <<EOF | sudo $KUBECTL --kubeconfig=$KUBECONFIG apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: 20Gi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: $STORAGE_PATH
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF

    if [[ $? -eq 0 ]]; then
        log_success "Created PV for Zarf registry"
    else
        log_warn "Failed to create PV - may already exist or not needed"
    fi

    return 0
}

# =============================================================================
# Step 6: Initialize Zarf on Cluster
# =============================================================================
initialize_zarf() {
    log_step "Step 6: Initializing Zarf on Cluster"

    if [[ "$SKIP_INIT" == "true" ]]; then
        log_info "Skipping Zarf init (--skip-init)"
        return 0
    fi

    # Create accessible kubeconfig
    local KUBECONFIG_TMP="/tmp/kubeconfig-zarf.yaml"
    sudo cp /etc/rancher/rke2/rke2.yaml "$KUBECONFIG_TMP"
    sudo chmod 644 "$KUBECONFIG_TMP"
    export KUBECONFIG="$KUBECONFIG_TMP"

    # Check if Zarf is already fully initialized (registry service exists and running)
    if kubectl get svc zarf-docker-registry -n zarf &>/dev/null; then
        local registry_pods=$(kubectl get pods -n zarf -l app=docker-registry -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
        if [[ "$registry_pods" == *"Running"* ]]; then
            log_success "Zarf already initialized - registry is running"
            kubectl get pods -n zarf
            return 0
        fi
    fi

    # Delete any partial initialization
    if kubectl get ns zarf &>/dev/null; then
        log_warn "Zarf namespace exists but registry not running - will re-initialize"
    fi

    cd "$ZARF_DIR"

    log_info "Running Zarf init..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would run: zarf init --confirm"
        return 0
    fi

    # Run Zarf init with detailed output
    if zarf init --confirm 2>&1 | tee /tmp/zarf-init.log | tail -30; then
        log_success "Zarf initialized successfully"
    else
        log_error "Zarf init failed - check /tmp/zarf-init.log for details"
        tail -50 /tmp/zarf-init.log
        return 1
    fi

    # Verify Zarf namespace
    if kubectl get ns zarf &>/dev/null; then
        log_success "Zarf namespace created"
        kubectl get pods -n zarf
    else
        log_error "Zarf namespace not found after init"
        return 1
    fi

    return 0
}

# =============================================================================
# Step 7: Build Zarf Package
# =============================================================================
build_zarf_package() {
    log_step "Step 7: Building Zarf Package"

    if [[ "$SKIP_BUILD" == "true" ]]; then
        log_info "Skipping package build (--skip-build)"
        return 0
    fi

    local PACKAGE_FILE="$ZARF_DIR/zarf-package-cybersec-dask-amd64-1.0.0.tar.zst"

    if [[ -f "$PACKAGE_FILE" ]]; then
        log_success "Zarf package already exists"
        ls -lh "$PACKAGE_FILE"
        return 0
    fi

    cd "$ZARF_DIR"

    log_info "Building Zarf package..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would run: zarf package create . --confirm"
        return 0
    fi

    if zarf package create . --confirm --insecure-skip-tls-verify 2>&1 | tee /tmp/zarf-build.log | tail -20; then
        log_success "Zarf package built"
        ls -lh "$ZARF_DIR"/*.tar.zst
    else
        log_error "Zarf package build failed - check /tmp/zarf-build.log"
        tail -30 /tmp/zarf-build.log
        return 1
    fi

    return 0
}

# =============================================================================
# Step 8: Deploy Zarf Package
# =============================================================================
deploy_zarf_package() {
    log_step "Step 8: Deploying Zarf Package"

    local KUBECONFIG_TMP="/tmp/kubeconfig-zarf.yaml"
    export KUBECONFIG="$KUBECONFIG_TMP"

    local PACKAGE_FILE="$ZARF_DIR/zarf-package-cybersec-dask-amd64-1.0.0.tar.zst"

    if [[ ! -f "$PACKAGE_FILE" ]]; then
        log_error "Zarf package not found: $PACKAGE_FILE"
        return 1
    fi

    cd "$ZARF_DIR"

    log_info "Deploying Zarf package..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would run: zarf package deploy $PACKAGE_FILE --confirm"
        return 0
    fi

    if zarf package deploy "$PACKAGE_FILE" --confirm 2>&1 | tee /tmp/zarf-deploy.log | tail -30; then
        log_success "Zarf package deployed"
    else
        log_error "Zarf deployment failed - check /tmp/zarf-deploy.log"
        tail -50 /tmp/zarf-deploy.log
        return 1
    fi

    return 0
}

# =============================================================================
# Step 8.5: Fix Image Tag Mismatch (Zarf suffix issue)
# =============================================================================
fix_image_tags() {
    log_step "Step 8.5: Fixing Image Tag Mismatch"

    # When Zarf init was done at a different time than package creation,
    # the agent mutations may use a different suffix than what was pushed.
    # This function detects and fixes this mismatch.

    local KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
    local KUBECONFIG="/etc/rancher/rke2/rke2.yaml"

    # Check if dask pods are in ImagePullBackOff
    local dask_pods_status=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask 2>/dev/null | grep -E "ImagePullBackOff|ErrImagePull" | wc -l)

    if [[ "$dask_pods_status" -eq 0 ]]; then
        log_success "No image pull issues detected"
        return 0
    fi

    log_warn "Detected ImagePullBackOff - checking for Zarf suffix mismatch..."

    # Get registry credentials
    local REG_INFO=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get secret -n zarf zarf-state -o jsonpath='{.data.state}' | base64 -d)
    local PUSH_USER=$(echo "$REG_INFO" | jq -r '.registryInfo.pushUsername')
    local PUSH_PASS=$(echo "$REG_INFO" | jq -r '.registryInfo.pushPassword')
    local PULL_USER=$(echo "$REG_INFO" | jq -r '.registryInfo.pullUsername')
    local PULL_PASS=$(echo "$REG_INFO" | jq -r '.registryInfo.pullPassword')
    local REG_ADDR=$(echo "$REG_INFO" | jq -r '.registryInfo.address')

    # Get what image the pods are trying to pull
    local EXPECTED_IMAGE=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get events -n dask --sort-by='.lastTimestamp' 2>/dev/null | grep "pulling image" | tail -1 | grep -oE '127\.0\.0\.1:[0-9]+/[^"]+' | head -1)

    if [[ -z "$EXPECTED_IMAGE" ]]; then
        log_warn "Could not determine expected image from events"
        return 1
    fi

    log_info "Pods expect: $EXPECTED_IMAGE"

    # Check what's actually in the registry
    local REPOS=$(curl -s -u "$PULL_USER:$PULL_PASS" "http://$REG_ADDR/v2/_catalog" | jq -r '.repositories[]' 2>/dev/null)

    for repo in cybersec-dask; do
        local ACTUAL_TAGS=$(curl -s -u "$PULL_USER:$PULL_PASS" "http://$REG_ADDR/v2/$repo/tags/list" | jq -r '.tags[]' 2>/dev/null | head -5)
        log_info "Registry has $repo tags: $ACTUAL_TAGS"

        # If expected image is library/cybersec-dask but registry has cybersec-dask
        if [[ "$EXPECTED_IMAGE" == *"library/cybersec-dask"* ]]; then
            # Extract expected tag (e.g., 2024.8.0-zarf-1346278550)
            local EXPECTED_TAG=$(echo "$EXPECTED_IMAGE" | grep -oE '[^:]+$')
            local BASE_TAG=$(echo "$EXPECTED_TAG" | sed 's/-zarf-[0-9]*//')

            log_info "Need to create library/cybersec-dask:$EXPECTED_TAG from cybersec-dask:$BASE_TAG"

            # Use podman to copy the image
            if command -v podman &>/dev/null; then
                podman login "$REG_ADDR" --username "$PUSH_USER" --password "$PUSH_PASS" --tls-verify=false 2>/dev/null

                log_info "Pulling cybersec-dask:$BASE_TAG..."
                podman pull "$REG_ADDR/cybersec-dask:$BASE_TAG" --tls-verify=false 2>&1 | tail -2

                log_info "Tagging and pushing to library/cybersec-dask:$EXPECTED_TAG..."
                podman tag "$REG_ADDR/cybersec-dask:$BASE_TAG" "$REG_ADDR/library/cybersec-dask:$EXPECTED_TAG"
                podman push "$REG_ADDR/library/cybersec-dask:$EXPECTED_TAG" --tls-verify=false 2>&1 | tail -2

                log_success "Created library/cybersec-dask:$EXPECTED_TAG"

                # Delete the stuck pods so they get recreated
                log_info "Restarting dask pods..."
                sudo $KUBECTL --kubeconfig=$KUBECONFIG delete pods -n dask --all 2>/dev/null

                # Wait for pods to come up
                sleep 15
                local new_status=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask 2>/dev/null)
                log_info "New pod status:\n$new_status"
            else
                log_error "Podman not available to fix image tags"
                return 1
            fi
        fi
    done

    return 0
}

# =============================================================================
# Step 9: Verify Deployment
# =============================================================================
verify_deployment() {
    log_step "Step 9: Verifying Deployment"

    local KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
    local KUBECONFIG="/etc/rancher/rke2/rke2.yaml"
    local verification_errors=0

    log_info "Checking namespaces..."
    sudo $KUBECTL --kubeconfig=$KUBECONFIG get ns

    # Check Zarf registry
    log_info "Checking Zarf registry..."
    local zarf_registry=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n zarf -l app=docker-registry -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
    if [[ "$zarf_registry" == *"Running"* ]]; then
        log_success "Zarf registry is running"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n zarf
    else
        log_error "Zarf registry not running"
        ((verification_errors++))
    fi

    # Check Dask operator
    log_info "Checking Dask operator..."
    local dask_operator=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask-operator -l app.kubernetes.io/name=dask-kubernetes-operator -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
    if [[ "$dask_operator" == *"Running"* ]]; then
        log_success "Dask operator is running"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask-operator
    else
        log_warn "Dask operator not running or not deployed"
    fi

    # Check DaskCluster
    log_info "Checking DaskCluster..."
    local dask_cluster=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get daskcluster -n dask -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
    if [[ "$dask_cluster" == "Running" ]]; then
        log_success "DaskCluster is running"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get daskcluster -n dask
    else
        log_warn "DaskCluster not running (status: $dask_cluster)"
    fi

    # Check Dask pods
    log_info "Checking Dask pods..."
    local dask_scheduler=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask -l app.kubernetes.io/name=dask-scheduler -o jsonpath='{.items[*].status.phase}' 2>/dev/null)
    if [[ "$dask_scheduler" == "Running" ]]; then
        log_success "Dask scheduler is running"
    else
        log_warn "Dask scheduler not running (status: $dask_scheduler)"
    fi
    sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask 2>/dev/null || true

    # Check Dask services
    log_info "Checking Dask services..."
    local dask_svc=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get svc -n dask -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
    if [[ -n "$dask_svc" ]]; then
        log_success "Dask services exist: $dask_svc"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get svc -n dask
    else
        log_warn "No Dask services found"
    fi

    # Check Dask dashboard connectivity
    log_info "Checking Dask dashboard connectivity..."
    local dashboard_port=$(sudo $KUBECTL --kubeconfig=$KUBECONFIG get svc -n dask -l app.kubernetes.io/name=cybersec-dask -o jsonpath='{.items[0].spec.ports[?(@.name=="tcp-dashboard")].nodePort}' 2>/dev/null)
    if [[ -n "$dashboard_port" ]]; then
        local dashboard_status=$(curl -sL -o /dev/null -w "%{http_code}" "http://127.0.0.1:$dashboard_port/status" 2>/dev/null || echo "000")
        if [[ "$dashboard_status" == "200" ]]; then
            log_success "Dask dashboard accessible at http://127.0.0.1:$dashboard_port"
        else
            log_warn "Dask dashboard returned HTTP $dashboard_status"
        fi
    else
        log_warn "Could not determine Dask dashboard port"
    fi

    # Check JupyterHub (optional)
    log_info "Checking JupyterHub..."
    if sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n jupyterhub 2>/dev/null | grep -q "Running"; then
        log_success "JupyterHub pods are running"
        sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n jupyterhub
    else
        log_info "JupyterHub not deployed (optional component)"
    fi

    # Summary
    echo ""
    echo "=============================================="
    log_info "Deployment Summary"
    echo "=============================================="
    echo "Zarf Registry:    $(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n zarf -l app=docker-registry -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'N/A')"
    echo "Dask Operator:    $(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask-operator -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'N/A')"
    echo "Dask Scheduler:   $(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask -l dask.org/component=scheduler -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'N/A')"
    echo "Dask Workers:     $(sudo $KUBECTL --kubeconfig=$KUBECONFIG get pods -n dask -l dask.org/component=worker --no-headers 2>/dev/null | wc -l) running"
    if [[ -n "$dashboard_port" ]]; then
        echo "Dashboard URL:    http://127.0.0.1:$dashboard_port"
    fi
    echo "=============================================="

    return $verification_errors
}

# =============================================================================
# Main Execution
# =============================================================================
main() {
    echo "=============================================="
    echo "Zarf Deployment Verification Script"
    echo "=============================================="
    echo "ZARF_DIR: $ZARF_DIR"
    echo "PROJECT_ROOT: $PROJECT_ROOT"
    echo "SKIP_INIT: $SKIP_INIT"
    echo "SKIP_BUILD: $SKIP_BUILD"
    echo "DRY_RUN: $DRY_RUN"
    echo "=============================================="

    local failed=0

    check_prerequisites || ((failed++))

    if [[ $failed -eq 0 ]]; then
        verify_injector_prerequisites || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        start_local_registry || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        build_custom_image || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        download_zarf_init || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        setup_storage || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        initialize_zarf || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        build_zarf_package || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        deploy_zarf_package || ((failed++))
    fi

    if [[ $failed -eq 0 ]]; then
        fix_image_tags || log_warn "Image tag fix may have failed, but continuing..."
    fi

    verify_deployment

    echo ""
    echo "=============================================="
    if [[ $failed -eq 0 ]]; then
        log_success "All steps completed successfully!"
    else
        log_error "Script failed at step $failed"
        exit 1
    fi
    echo "=============================================="
}

main "$@"
