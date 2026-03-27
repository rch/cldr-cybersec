# Cybersec Dask — Air-Gap Deployment Runbook

**Package**: `cybersec-dask` v1.3.0
**Target**: RKE2 cluster (bare metal or cloud, no internet required at deploy time)

---

## Prerequisites

- RKE2 cluster running with `kubectl` access
- `root` or `sudo` on the control plane node
- Three files transferred to the control plane (see Phase 1)

---

## Phase 1: Acquire Artifacts (Connected Machine)

```bash
# 1. Zarf binary
curl -LO https://github.com/zarf-dev/zarf/releases/download/v0.74.0/zarf_v0.74.0_Linux_amd64
mv zarf_v0.74.0_Linux_amd64 zarf && chmod +x zarf

# 2. Zarf init package
./zarf tools download-init
# produces: zarf-init-amd64-v0.74.0.tar.zst (~390 MB)

# 3. Cybersec Dask package (build from source or download release)
# Option A: Build
git clone https://github.com/cloudera/cybersec && cd cybersec
zarf package create zarf/ --confirm
# produces: zarf/zarf-package-cybersec-dask-amd64-1.3.0.tar.zst (~1.3 GB)

# Option B: Download from GitHub Releases
# https://github.com/rch/cldr-cybersec/releases/tag/zarf-v1.3.0

# 4. Vendored StorageClass manifest (included in repo)
cp zarf/manifests/local-path-provisioner.yaml .
```

### Transfer Checklist

| File | Size | Required |
|------|------|----------|
| `zarf` (binary) | ~100 MB | Yes |
| `zarf-init-amd64-v0.74.0.tar.zst` | ~390 MB | Yes |
| `zarf-package-cybersec-dask-amd64-1.3.0.tar.zst` | ~1.3 GB | Yes |
| `local-path-provisioner.yaml` | 5 KB | Yes (if no default StorageClass) |

Transfer all files to the control plane node via USB, SCP, data diode, etc.

---

## Phase 2: Deploy (Air-Gapped Control Plane)

All commands run as `root` (or prefix with `sudo`).

### Step 1 — Environment Setup

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
export PATH=$PATH:/var/lib/rancher/rke2/bin

# Install Zarf binary
cp zarf /usr/local/bin/zarf
chmod +x /usr/local/bin/zarf
zarf version
```

### Step 2 — Verify Cluster Health

```bash
kubectl get nodes
# All nodes should be Ready

kubectl get storageclass
# Note: if no default StorageClass is listed, Step 3 is required
```

### Step 3 — Install StorageClass (skip if one already exists)

Bare RKE2 has no default StorageClass. Without one, Zarf's registry PVC
stays Pending forever.

```bash
kubectl apply -f local-path-provisioner.yaml

# Wait for provisioner to be ready
kubectl wait --for=condition=ready pod -l app=local-path-provisioner \
  -n local-path-storage --timeout=60s

# Verify it's the default
kubectl get storageclass
# NAME                   PROVISIONER             AGE
# local-path (default)   rancher.io/local-path   10s
```

### Step 4 — Zarf Init

Zarf auto-detects the init package by looking for `zarf-init-amd64-*.tar.zst`
in the current directory. If not found, it attempts to download from GitHub
(which will fail in air-gap).

```bash
cd /path/to/artifacts
ls zarf-init-amd64-*.tar.zst   # confirm init package is here

zarf init --confirm --set REGISTRY_PVC_SIZE=1Gi

# Wait ~2-3 minutes. Verify:
kubectl get pods -n zarf
# NAME                                      READY   STATUS
# zarf-docker-registry-*                    1/1     Running
# agent-hook-*                              1/1     Running
```

### Step 5 — Deploy Cybersec Dask

```bash
zarf package deploy zarf-package-cybersec-dask-amd64-1.3.0.tar.zst \
  --confirm \
  --set DASK_WORKER_REPLICAS=4 \
  --set DASK_SPILL_DIR=/tmp/dask-spill
```

**Optional S3 variables** (if connecting to S3-compatible storage):

```bash
zarf package deploy zarf-package-cybersec-dask-amd64-1.3.0.tar.zst \
  --confirm \
  --set DASK_WORKER_REPLICAS=4 \
  --set DASK_SPILL_DIR=/tmp/dask-spill \
  --set S3_ENDPOINT=http://minio:9000 \
  --set S3_BUCKET=cybersec-data \
  --set S3_REGION=us-east-1 \
  --set S3_ACCESS_KEY=<access-key> \
  --set S3_SECRET_KEY=<secret-key>
```

Deployment takes ~5-15 minutes (image push to internal registry is the bottleneck).

---

## Phase 3: Verify

### Quick Check

```bash
# All pods running
kubectl get pods -A | grep -E 'dask|jupyter|panel-viz|local-path|zarf'
```

### Expected State

```bash
# Dask
kubectl get pods -n dask
# cybersec-dask-scheduler-*     1/1   Running
# cybersec-dask-worker-*        1/1   Running   (×DASK_WORKER_REPLICAS)

# JupyterHub
kubectl get pods -n jupyterhub
# hub-*                         1/1   Running
# proxy-*                       1/1   Running

# Panel-Viz (OTEL Navigator)
kubectl get pods -n panel-viz
# otel-navigator-*              2/2   Running   (app + PTY proxy sidecar)
# navigator-engine-*            1/1   Running

# Dask cluster health
kubectl exec -n dask deploy/cybersec-dask-scheduler -- \
  python -c "from distributed import Client; c=Client('localhost:8786'); print(c)"
```

### Service Ports (NodePort)

| Service | NodePort | Description |
|---------|----------|-------------|
| Dask Scheduler | 30086 | Client connections |
| Dask Dashboard | 30087 | Web UI |
| JupyterHub | 30080 | Web UI |
| Panel-Viz | 30506 | OTEL Navigator |

Access from any node IP: `http://<node-ip>:<nodeport>`

---

## Troubleshooting

### Clean up previous Zarf state before re-init

If a previous `zarf init` or `zarf package deploy` was run on this cluster,
you must clean it first. Common error: `Forbidden: field cannot be less than
previous value` (PVC size mismatch from earlier init).

```bash
# Remove previous app package (if any)
zarf package remove cybersec-dask --confirm 2>/dev/null || true

# Remove previous Zarf init
zarf destroy --confirm 2>/dev/null || true

# If zarf destroy fails, clean manually:
kubectl delete namespace zarf --wait=false 2>/dev/null || true
kubectl delete pvc -n zarf zarf-docker-registry 2>/dev/null || true
kubectl delete pv -l app=docker-registry 2>/dev/null || true

# Wait for namespace to terminate (may need finalizer cleanup)
kubectl get ns zarf 2>/dev/null && \
  kubectl patch ns zarf -p '{"metadata":{"finalizers":null}}' --type=merge

# Verify clean
kubectl get ns zarf 2>&1 | grep -q "not found" && echo "Clean"

# Now retry Step 4
```

### Zarf init PVC stuck Pending

```bash
# Check if StorageClass exists
kubectl get storageclass

# If none: install local-path-provisioner (Step 3)
# If stuck from failed previous attempt, use the cleanup steps above
```

### Image push timeout during deploy

```bash
# Retry — layers are cached from first attempt
zarf package deploy zarf-package-cybersec-dask-*.tar.zst --confirm \
  --set DASK_WORKER_REPLICAS=4
```

### Pod in ImagePullBackOff

```bash
# Check if image is in Zarf registry
kubectl get pods -n zarf -l app=docker-registry
zarf tools registry catalog 127.0.0.1:31999

# If image missing, redeploy the component
zarf package deploy zarf-package-cybersec-dask-*.tar.zst --confirm \
  --components=cybersec-images
```

### Disk-constrained node

Use a small hostPath PV instead of the dynamic provisioner:

```bash
mkdir -p /var/lib/zarf-registry && chmod 777 /var/lib/zarf-registry
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: 5Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /var/lib/zarf-registry
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF

zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi
```

---

## Teardown

```bash
# Remove application package
zarf package remove cybersec-dask --confirm

# Remove Zarf itself
zarf destroy --confirm

# Remove StorageClass (if installed)
kubectl delete -f local-path-provisioner.yaml
```

---

## Deploy Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DASK_WORKER_REPLICAS` | `4` | Number of Dask workers |
| `DASK_SPILL_DIR` | `/tmp/dask-spill` | Host path for spill-to-disk (NFS recommended) |
| `S3_ENDPOINT` | _(empty)_ | S3-compatible endpoint URL |
| `S3_BUCKET` | `cybersec-dask-data` | Bucket name for OTEL data |
| `S3_REGION` | `us-east-1` | AWS region |
| `S3_ACCESS_KEY` | _(empty)_ | S3 access key |
| `S3_SECRET_KEY` | _(empty)_ | S3 secret key |
| `S3_SESSION_TOKEN` | _(empty)_ | AWS STS session token |
| `INGRESS_CLASS` | `traefik` | Ingress controller class |
| `INGRESS_DOMAIN` | `cybersec.local` | Base domain for ingress |

## Components

All optional components deploy by default. Skip with `--components`:

```bash
# Deploy without JupyterHub
zarf package deploy *.tar.zst --confirm \
  --components=local-path-provisioner,cybersec-images,dask-operator,dask-cluster,panel-viz,navigator-engine
```

| Component | Default | Skip-safe? |
|-----------|---------|------------|
| local-path-provisioner | on | Yes (if StorageClass exists) |
| cybersec-images | required | No |
| dask-operator | required | No |
| dask-cluster | required | No |
| jupyterhub | on | Yes |
| panel-viz | on | Yes |
| navigator-engine | on | Yes |
| sample-notebooks | on | Yes |
| ingress | on | Yes (if no ingress controller) |
