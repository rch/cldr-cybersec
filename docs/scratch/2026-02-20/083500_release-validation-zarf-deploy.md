# Release Validation: Zarf Air-Gap Deploy on RKE2

**Date**: 2026-02-20
**Target**: tinybox (RKE2 v1.34.3+rke2r3, Ubuntu 22.04, 916G system drive)
**Package**: cybersec-dask-amd64-1.1.1 (split manifests + ghostty-web image fix)

## Summary

Full clean deploy of cybersec-dask Zarf package validated on tinybox RKE2.
Two bugs found and fixed during validation:

1. **DaskCluster CRD timing** — Zarf raw manifest wrapper silently fails to
   create CRD instances when bundled with namespaces in a single Helm release.
   **Fix**: Split `dask-namespace-and-cluster` into `dask-namespaces` +
   `dask-cluster-cr` in `zarf.yaml`.

2. **Missing ghostty-web static files** — Image in previous Zarf package was
   built before the ghostty Dockerfile changes were added. Panel-viz crashed
   with `Cannot serve non-existent path /app/static/ghostty`.
   **Fix**: Rebuild image from current Dockerfile (which downloads ghostty-web
   from NPM at build time).

3. **Stale containerd cache** — After rebuilding the image and package, the
   node's containerd still served the OLD image because `imagePullPolicy:
   IfNotPresent` and the Zarf tag suffix didn't change.
   **Fix**: Clear containerd cache with `crictl rmi`.

## Prerequisites

- RKE2 cluster with KUBECONFIG at `~/.kube/rke2.yaml`
- Podman socket running (for `zarf package create`)
- Zarf initialized with registry PV

## Phase 1: Build Image

```bash
# Start Podman socket (if not running)
podman system service --time=0 unix:///run/user/$(id -u)/podman/podman.sock &
export DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock

# Build image from project root (downloads ghostty-web from NPM during build)
cd /home/rch/local/src/cldr/cybersec
podman build \
  -t cybersec-dask:2025.2.0 \
  -f zarf/images/Dockerfile.cybersec-dask \
  --build-arg BASE_IMAGE=ghcr.io/dask/dask:2025.2.0 \
  .

# Tag for Zarf
podman tag cybersec-dask:2025.2.0 localhost:5555/cybersec-dask:2025.2.0

# Verify ghostty files are baked in
podman run --rm cybersec-dask:2025.2.0 ls -la /app/static/ghostty/
# Expected: ghostty-web.js, ghostty-vt.wasm, loader.js, etc.
```

## Phase 2: Build Zarf Package

```bash
cd /home/rch/local/src/cldr/cybersec/zarf

# Remove old package
rm -f zarf-package-cybersec-dask-amd64-*.tar.zst

# Create package (~26 min for image pulls from Podman)
DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock \
  zarf package create --confirm
```

## Phase 3: Initialize Zarf (if needed)

```bash
export KUBECONFIG=~/.kube/rke2.yaml

# Create registry directory
sudo mkdir -p /var/lib/zarf-registry
sudo chown 1000:2000 /var/lib/zarf-registry

# Pre-create hostPath PV with claimRef
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: 5Gi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF

# Init Zarf
zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi
```

## Phase 4: Clean Deploy

```bash
# Remove any existing deployment
zarf package remove zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm 2>/dev/null

# Clean up namespaces and CRDs
kubectl delete ns dask dask-operator panel-viz jupyterhub --timeout=60s 2>/dev/null
kubectl delete crd --selector=app.kubernetes.io/name=dask-kubernetes-operator 2>/dev/null

# Deploy
zarf package deploy zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_WORKER_REPLICAS=4 \
  --set DASK_SPILL_DIR=/raid/dask-spill \
  --set S3_ENDPOINT=http://192.168.1.55:9010 \
  --set S3_BUCKET=cybersec \
  --set S3_ACCESS_KEY=minioadmin \
  --set S3_SECRET_KEY=minioadmin
```

## Phase 5: Stale Image Cache Fix (if upgrading)

If upgrading from a previous deploy with the same image tag, containerd caches
the old image. The new image won't be pulled because `imagePullPolicy: IfNotPresent`.

```bash
# Find the Zarf-rewritten image tag
crictl_cmd="sudo /var/lib/rancher/rke2/bin/crictl -r unix:///run/k3s/containerd/containerd.sock"

# List cached cybersec-dask images
$crictl_cmd images | grep cybersec-dask

# Remove stale cached image
$crictl_cmd rmi 127.0.0.1:31999/cybersec-dask:2025.2.0-zarf-2560517462

# Restart pods that use the image
kubectl delete pod -n panel-viz -l app=otel-navigator
kubectl delete pod -n panel-viz -l app=navigator-engine
# Dask workers use the same image but don't need ghostty
```

## Phase 6: Verification

```bash
# All pods Running
kubectl get pods -A | grep -v kube-system | grep -v local-path

# Service endpoints
curl -s -o /dev/null -w "Dask Dashboard: %{http_code}\n" http://192.168.1.55:30087/
curl -s -o /dev/null -w "Panel-Viz: %{http_code}\n" http://192.168.1.55:30506/
curl -s -o /dev/null -w "JupyterHub: %{http_code}\n" http://192.168.1.55:30080/

# Ghostty static files
curl -s -o /dev/null -w "ghostty loader: %{http_code}\n" http://192.168.1.55:30506/ghostty/loader.js
curl -s -o /dev/null -w "ghostty wasm: %{http_code}\n" http://192.168.1.55:30506/ghostty/ghostty-vt.wasm

# Dask connectivity from navigator pod
kubectl exec -n panel-viz deploy/navigator-engine -- python -c \
  "from distributed import Client; c = Client('tcp://cybersec-dask-scheduler.dask.svc.cluster.local:8786', timeout='5s'); print(f'Workers: {len(c.scheduler_info()[\"workers\"])}')"

# DiskPressure
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: DiskPressure={range .status.conditions[?(@.type=="DiskPressure")]}{.status}{end}{"\n"}{end}'

# Disk usage
df -h /
```

## Results

| Component | Status | Pods | Notes |
|-----------|--------|------|-------|
| dask-operator | Running | 1/1 | Helm chart install |
| dask-cluster | Running | 6/6 | 1 scheduler + 4 workers (split manifest fix) |
| jupyterhub | Running | 2/2 | hub + proxy |
| panel-viz | Running | 2/2 | otel-navigator (2 containers: panel + pty-proxy) |
| navigator-engine | Running | 1/1 | gRPC server |
| sample-notebooks | Applied | N/A | ConfigMap in jupyterhub ns |
| ingress | Applied | N/A | Ingress resources |
| kubernetes-dashboard | Running | 1/1 | Separate package |

**Total**: 14 pods Running, 0 failures

### Service Endpoints

| Service | Port | HTTP Status |
|---------|------|-------------|
| Dask Dashboard | 30087 | 301 (redirect) |
| Panel-Viz | 30506 | 302 (redirect) |
| JupyterHub | 30080 | 302 (redirect) |
| ghostty loader.js | 30506/ghostty/ | 200 |
| ghostty-vt.wasm | 30506/ghostty/ | 200 |

### Disk

| Metric | Value |
|--------|-------|
| System drive | /dev/sda2: 916G, 84% used, 147G free |
| DiskPressure | False |
| Node status | Ready |

## Key Findings

### 1. Split Manifests Fix (zarf.yaml)

The DaskCluster CRD instance must be in a SEPARATE Helm release from the
namespace definition. When combined, Zarf's raw chart wrapper can silently
fail to create the CRD instance.

```yaml
# BEFORE (broken):
manifests:
  - name: dask-namespace-and-cluster
    files:
      - manifests/namespace.yaml
      - manifests/dask-cluster.yaml

# AFTER (working):
manifests:
  - name: dask-namespaces
    files:
      - manifests/namespace.yaml
  - name: dask-cluster-cr
    files:
      - manifests/dask-cluster.yaml
```

### 2. containerd Stale Image Cache

When upgrading a Zarf package with the same image tag but different content,
containerd won't re-pull because `imagePullPolicy: IfNotPresent` and the Zarf
tag suffix (CRC32 of image reference) is identical. Must use `crictl rmi` to
clear the cached image.

**For air-gap environments**: This only matters during UPGRADES. Fresh deploys
on clean nodes won't have this issue.

### 3. Zombie CRD Resources After Force-Finalized Namespaces

When force-finalizing a namespace (patching `spec.finalizers` to null), CRD
instances with their own finalizers (e.g., kopf) can become orphaned in etcd.
When a new namespace with the same name is created, the zombie resources
reappear with stale Helm annotations, causing ownership conflicts.

**Cleanup**: Always delete CRD instances and patch their finalizers BEFORE
force-finalizing the namespace.

### 4. Kopf Finalizer Cascade

Removing the kopf finalizer from a DaskCluster can trigger a cascade that
deletes the DaskCluster CRD itself (not just the instance). Always use
`zarf package remove` for clean teardown instead of manually patching finalizers.
