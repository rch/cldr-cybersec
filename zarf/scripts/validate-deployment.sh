#!/bin/bash
# Validate OTEL Navigator + Dask deployment on RKE2
#
# This script verifies:
# 1. All pods are running
# 2. Dask scheduler is reachable
# 3. OTEL Navigator is serving
# 4. (Optional) Data loading works
#
# Usage:
#   ./validate-deployment.sh           # Basic validation
#   ./validate-deployment.sh --full    # Include data loading test

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

FULL_TEST=false
if [[ "$1" == "--full" ]]; then
    FULL_TEST=true
fi

echo "=============================================="
echo "OTEL Navigator + Dask Deployment Validation"
echo "=============================================="
echo ""

# Check kubectl connectivity
log_info "Checking kubectl connectivity..."
if ! kubectl cluster-info &>/dev/null; then
    log_error "Cannot connect to Kubernetes cluster"
    exit 1
fi
log_success "Connected to Kubernetes cluster"

# Check namespaces
log_info "Checking namespaces..."
MISSING_NS=""
for ns in dask panel-viz; do
    if kubectl get ns "$ns" &>/dev/null; then
        log_success "Namespace $ns exists"
    else
        log_error "Namespace $ns not found"
        MISSING_NS="$MISSING_NS $ns"
    fi
done

if [[ -n "$MISSING_NS" ]]; then
    log_error "Missing namespaces:$MISSING_NS"
    exit 1
fi

# Check Dask pods
echo ""
log_info "Checking Dask pods..."
DASK_SCHEDULER=$(kubectl get pods -n dask -l dask.org/component=scheduler -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [[ -z "$DASK_SCHEDULER" ]]; then
    log_error "Dask scheduler pod not found"
    exit 1
fi

SCHEDULER_STATUS=$(kubectl get pod -n dask "$DASK_SCHEDULER" -o jsonpath='{.status.phase}')
if [[ "$SCHEDULER_STATUS" == "Running" ]]; then
    log_success "Dask scheduler running: $DASK_SCHEDULER"
else
    log_error "Dask scheduler not running: $SCHEDULER_STATUS"
    exit 1
fi

DASK_WORKERS=$(kubectl get pods -n dask -l dask.org/component=worker --no-headers 2>/dev/null | wc -l)
if [[ "$DASK_WORKERS" -gt 0 ]]; then
    log_success "Dask workers running: $DASK_WORKERS"
else
    log_warn "No Dask workers found (cluster may still be starting)"
fi

# Check OTEL Navigator pod
echo ""
log_info "Checking OTEL Navigator pod..."
NAVIGATOR_POD=$(kubectl get pods -n panel-viz -l app=otel-navigator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [[ -z "$NAVIGATOR_POD" ]]; then
    log_error "OTEL Navigator pod not found"
    exit 1
fi

NAVIGATOR_STATUS=$(kubectl get pod -n panel-viz "$NAVIGATOR_POD" -o jsonpath='{.status.phase}')
NAVIGATOR_READY=$(kubectl get pod -n panel-viz "$NAVIGATOR_POD" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
if [[ "$NAVIGATOR_STATUS" == "Running" && "$NAVIGATOR_READY" == "True" ]]; then
    log_success "OTEL Navigator running and ready: $NAVIGATOR_POD"
else
    log_warn "OTEL Navigator status: $NAVIGATOR_STATUS (Ready: $NAVIGATOR_READY)"
    log_info "Checking pod events..."
    kubectl describe pod -n panel-viz "$NAVIGATOR_POD" | tail -20
fi

# Check services
echo ""
log_info "Checking services..."
DASK_SVC=$(kubectl get svc -n dask cybersec-dask-scheduler -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
if [[ -n "$DASK_SVC" ]]; then
    log_success "Dask scheduler service: $DASK_SVC:8786"
else
    log_warn "Dask scheduler service not found"
fi

NAVIGATOR_NODEPORT=$(kubectl get svc -n panel-viz otel-navigator -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
if [[ -n "$NAVIGATOR_NODEPORT" ]]; then
    log_success "OTEL Navigator NodePort: $NAVIGATOR_NODEPORT"
else
    log_warn "OTEL Navigator service not found"
fi

# Get node IP for access
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "localhost")

# Test HTTP connectivity to OTEL Navigator
echo ""
log_info "Testing OTEL Navigator HTTP endpoint..."
if kubectl exec -n panel-viz "$NAVIGATOR_POD" -- curl -s -o /dev/null -w "%{http_code}" http://localhost:5006/ | grep -q "200"; then
    log_success "OTEL Navigator responding on port 5006"
else
    log_warn "OTEL Navigator not responding yet (may still be starting)"
fi

# Full test: Check Dask connectivity from Navigator
if [[ "$FULL_TEST" == "true" ]]; then
    echo ""
    log_info "Running full connectivity test..."

    # Test Dask connection from Navigator pod
    log_info "Testing Dask connection from OTEL Navigator..."
    kubectl exec -n panel-viz "$NAVIGATOR_POD" -- python -c "
from dask.distributed import Client
import os

scheduler = os.environ.get('DASK_SCHEDULER', 'tcp://cybersec-dask-scheduler.dask.svc.cluster.local:8786')
print(f'Connecting to: {scheduler}')

try:
    client = Client(scheduler, timeout='10s')
    info = client.scheduler_info()
    workers = len(info.get('workers', {}))
    print(f'Connected! Workers: {workers}')
    client.close()
    exit(0)
except Exception as e:
    print(f'Connection failed: {e}')
    exit(1)
" && log_success "Dask connection successful" || log_error "Dask connection failed"

    # Test S3 connectivity (if credentials are set)
    log_info "Testing S3 connectivity..."
    kubectl exec -n panel-viz "$NAVIGATOR_POD" -- python -c "
import os
import s3fs

endpoint = os.environ.get('S3_ENDPOINT', '')
bucket = os.environ.get('S3_BUCKET', 'cybersec-dask-data')
key = os.environ.get('AWS_ACCESS_KEY_ID', '')
secret = os.environ.get('AWS_SECRET_ACCESS_KEY', '')

if not key or not secret:
    print('S3 credentials not configured - skipping')
    exit(0)

opts = {'key': key, 'secret': secret}
if endpoint:
    opts['client_kwargs'] = {'endpoint_url': endpoint}

try:
    fs = s3fs.S3FileSystem(**opts)
    # Just check if we can list the bucket
    contents = fs.ls(bucket, detail=False)
    print(f'S3 bucket accessible: {len(contents)} items')
    exit(0)
except Exception as e:
    print(f'S3 access failed: {e}')
    exit(1)
" && log_success "S3 connection successful" || log_warn "S3 connection failed or not configured"
fi

# Summary
echo ""
echo "=============================================="
echo "Validation Summary"
echo "=============================================="
echo ""
log_success "Dask scheduler: Running"
log_success "Dask workers: $DASK_WORKERS"
log_success "OTEL Navigator: $NAVIGATOR_STATUS"
echo ""
echo "Access OTEL Navigator:"
echo "  NodePort: http://$NODE_IP:$NAVIGATOR_NODEPORT"
echo ""
echo "Access Dask Dashboard:"
DASK_NODEPORT=$(kubectl get svc -n dask cybersec-dask-scheduler -o jsonpath='{.spec.ports[?(@.name=="tcp-dashboard")].nodePort}' 2>/dev/null || echo "30087")
echo "  NodePort: http://$NODE_IP:$DASK_NODEPORT"
echo ""

if [[ "$FULL_TEST" != "true" ]]; then
    echo "Run with --full for connectivity tests"
fi
