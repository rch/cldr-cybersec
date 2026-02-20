# Disk-Pressure Zarf Test — Tinybox RKE2

**Date**: 2026-02-20
**Node**: tinybox (64-core, 128GB RAM, Ubuntu 22.04, RKE2 v1.34.3+rke2r3)
**Goal**: Validate zarf init + deploy cycle under ~93% disk pressure
**Result**: PASS (with one workaround for DaskCluster CRD timing)

## Phase 0: Baseline Snapshot

```
$ df -h / /raid
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda2       916G  734G  145G  84% /
/dev/md0        3.6T  3.1T  362G  90% /raid
```

- **Node**: Ready=True, DiskPressure=False, MemoryPressure=False
- **Kubelet eviction (defaults)**: nodefs.available=5%, imagefs.available=5%
- **No /etc/rancher/rke2/config.yaml** (using RKE2 defaults)
- **PVs**: zarf-registry (1Gi local-path Bound), jupyterhub hub-db-dir (1Gi Bound), zarf-registry-pv (20Gi hostPath Released/stale)
- **Pods**: All Running — zarf, dask (4 workers), jupyterhub, panel-viz (navigator), k8s-dashboard

---

## Phase 1: Create Disk Pressure (88G Balloon)

```bash
# Initial attempt: 118G balloon brought disk to 98% — triggered DiskPressure
# with default 5% threshold. Corrected to 88G for ~93% target.
sudo dd if=/dev/urandom of=/var/tmp/disk-pressure-balloon.dat bs=1G count=88 status=progress

# Or if overshot, truncate:
sudo truncate --size=88G /var/tmp/disk-pressure-balloon.dat
```

**Result**: `df -h /` → 916G, 814G used, 65G free (93%)

**Key learning**: The balloon size depends on CURRENT usage, not baseline. Account
for growth since the plan was written. Target 7% free (above the 5% soft eviction
threshold) before applying custom thresholds.

---

## Phase 2: Configure RKE2 Kubelet Eviction Thresholds

```bash
sudo mkdir -p /etc/rancher/rke2
sudo tee /etc/rancher/rke2/config.yaml > /dev/null << 'EOF'
kubelet-arg:
  - "eviction-hard=nodefs.available<2%,imagefs.available<2%,memory.available<500Mi"
  - "eviction-soft=nodefs.available<5%,imagefs.available<5%,memory.available<1Gi"
  - "eviction-soft-grace-period=nodefs.available=2m,imagefs.available=2m,memory.available=2m"
  - "eviction-minimum-reclaim=nodefs.available=500Mi,imagefs.available=500Mi,memory.available=500Mi"
  - "image-gc-high-threshold=99"
  - "image-gc-low-threshold=98"
EOF
```

**Key setting**: `image-gc-high-threshold=99` prevents containerd from GC'ing
cached images during restart, which would cause ImagePullBackOff in air-gap.

**How it works**: CLI `--kubelet-arg` flags override the KubeletConfiguration
file (`/var/lib/rancher/rke2/agent/etc/kubelet.conf.d/00-rke2-defaults.conf`).
Verified via kubelet configz endpoint:
```
evictionHard.nodefs.available: "2%"    (was 5%)
evictionHard.imagefs.available: "2%"   (was 5%)
```

---

## Phase 3: Restart RKE2

```bash
sudo systemctl restart rke2-server

# Wait for API server (usually <30s)
until kubectl get nodes &>/dev/null; do sleep 5; done

# Wait for node Ready
kubectl wait --for=condition=Ready node/tinybox --timeout=300s
```

**DiskPressure transition**: After restart, DiskPressure remained True for
~5 minutes (`evictionPressureTransitionPeriod: 5m0s`). This is expected —
kubelet won't clear the taint until the condition has been false for the full
transition period.

**Pod evictions during DiskPressure**: Before the custom thresholds took effect,
the default 5% threshold triggered DiskPressure, evicting pods across all
namespaces. These were cleaned up with:
```bash
kubectl delete pods -A --field-selector=status.phase=Failed
```
Replacement pods spun up automatically once DiskPressure cleared.

---

## Phase 4: Tear Down Existing Zarf State

```bash
# 1. Delete application namespaces first
kubectl delete namespace kubernetes-dashboard panel-viz jupyterhub dask dask-operator

# 2. Force-finalize stuck namespaces (DaskCluster kopf finalizer)
kubectl get namespace dask -o json > /tmp/dask-ns.json
python3 -c "import json; d=json.load(open('/tmp/dask-ns.json')); d['spec']['finalizers']=[]; json.dump(d, open('/tmp/dask-ns-clean.json','w'))"
kubectl replace --raw /api/v1/namespaces/dask/finalize -f /tmp/dask-ns-clean.json

# 3. Destroy Zarf
cd /path/to/cybersec/zarf
zarf destroy --confirm --remove-components

# 4. Clean up stale PV
kubectl patch pv zarf-registry-pv -p '{"metadata":{"finalizers":null}}'
kubectl delete pv zarf-registry-pv --force --grace-period=0

# 5. Clean registry data
sudo rm -rf /var/lib/zarf-registry/*

# 6. Verify local-path-storage survived (not managed by Zarf)
kubectl get pods -n local-path-storage
```

---

## Phase 5: Re-Initialize Zarf (Disk-Light)

```bash
# 1. Prepare registry directory
sudo mkdir -p /var/lib/zarf-registry
sudo chown 1000:2000 /var/lib/zarf-registry

# 2. Pre-create 5Gi hostPath PV with claimRef
kubectl apply -f - << 'EOF'
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

# 3. Initialize Zarf with 5Gi PVC
zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi
```

**Result**: Registry pod Running, PVC Bound to 5Gi hostPath PV. ~1 minute total.

---

## Phase 6: Deploy cybersec-dask Package

```bash
zarf package deploy zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_WORKER_REPLICAS=4 \
  --set DASK_SPILL_DIR=/raid/dask-spill \
  --set S3_ENDPOINT=http://<NODE_IP>:9010 \
  --set S3_BUCKET=cybersec \
  --set S3_ACCESS_KEY=minioadmin \
  --set S3_SECRET_KEY=minioadmin
```

**Bug encountered**: First deploy attempt timed out waiting for DaskCluster.
The Helm release was created with the DaskCluster manifest, but the actual
DaskCluster CR wasn't created (Zarf health check: "DaskCluster not found").

**Workaround**: Manually re-apply from the Helm release, then re-run deploy:
```bash
# Extract and apply the manifest
helm get manifest <zarf-release-name> -n default | kubectl apply -f -

# Wait for scheduler to come up, then re-run deploy
zarf package deploy ... --confirm  # Second run succeeds (idempotent)
```

The second deploy was fully idempotent — all components upgraded/installed
successfully: dask-operator, dask-cluster (scheduler Ready), jupyterhub
(hub Ready), panel-viz (otel-navigator Ready), navigator-engine, sample-notebooks,
ingress.

---

## Phase 7: Deploy K8s Dashboard Package

```bash
cd kubernetes-dashboard
zarf package deploy zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst --confirm
```

**Result**: Dashboard + metrics-scraper Running in ~10 seconds.

---

## Phase 8: Full Verification

### All Pods Running (28 total)

| Namespace | Pod | Ready |
|-----------|-----|-------|
| zarf | registry, agent-hook x2 | 1/1, 1/1, 1/1 |
| dask-operator | operator | 1/1 |
| dask | scheduler + 4 workers | 1/1 x5 |
| jupyterhub | hub, proxy | 1/1 x2 |
| panel-viz | otel-navigator (2/2), navigator-engine | 2/2, 1/1 |
| kubernetes-dashboard | dashboard, metrics-scraper | 1/1 x2 |
| local-path-storage | provisioner | 1/1 |
| kube-system | 10 pods (standard RKE2) | All Running |

### Service Connectivity

| Service | Port | Status |
|---------|------|--------|
| Dask Dashboard | 30087 | 301 (redirect to /status) |
| Dask Scheduler | 30086 | TCP connected |
| Panel-Viz (OTEL Navigator) | 30506 | 302 (redirect to login) |
| JupyterHub | 30080 | 302 (redirect to login) |
| K8s Dashboard | ClusterIP only | By design |

### Storage

| Resource | Status |
|----------|--------|
| DiskPressure | **False** at 94% disk |
| zarf-registry PV | Bound, 5Gi hostPath |
| jupyterhub hub-db PVC | Bound, 1Gi local-path |
| Dask spill | /dev/md0 mounted at /dask-spill (362G avail) |

### Dask Connectivity

```
$ kubectl exec -n panel-viz deployment/navigator-engine -- python3 -c "
from dask.distributed import Client
c = Client('tcp://cybersec-dask-scheduler.dask.svc.cluster.local:8786', timeout=10)
print(f'Dask connected: {len(c.scheduler_info()[\"workers\"])} workers')
c.close()"

Dask connected: 4 workers
```

---

## Phase 9: Cleanup

```bash
sudo rm /var/tmp/disk-pressure-balloon.dat
```

**Result**: Disk returned to 84% (148G free). All pods remained Running.

---

## Key Findings

### 1. Balloon Size Must Account for Current Usage
The plan estimated 118G to reach 93%, but actual baseline had grown to 726G
(from writes during earlier sessions). Overshot to 98%, triggering DiskPressure
before custom thresholds were applied. **Always check `df -h` immediately before
creating the balloon.**

### 2. Soft Eviction Threshold Matters
DiskPressure is set when EITHER the hard OR soft eviction threshold is crossed.
With default thresholds (hard=5%, soft not explicitly set but inherited), going
below 5% free triggers DiskPressure. The custom config sets:
- Hard: 2% (absolute minimum)
- Soft: 5% with 2-minute grace period

### 3. evictionPressureTransitionPeriod = 5 Minutes
After crossing back above the threshold, kubelet waits the full transition
period before clearing DiskPressure. Budget this wait time into deployment.

### 4. image-gc-high-threshold=99 Is Critical for Air-Gap
Without this, containerd will garbage-collect cached container images during
restart when disk is above the default 85% threshold. In air-gap, this causes
ImagePullBackOff since images can't be re-pulled.

### 5. DaskCluster CRD Timing Issue
Zarf's health check for the DaskCluster component can fail if the operator
hasn't reconciled the CR fast enough. The Helm release applies the manifest
but Zarf's wait-for-ready times out before the operator creates the scheduler
pod. Workaround: manually apply the manifest and re-run deploy.

### 6. Disk Budget at Peak (94%)

| Component | Size |
|-----------|------|
| Baseline (OS, RKE2, user data) | 726G |
| Balloon (simulated pressure) | 88G |
| Zarf registry | ~2G |
| Temp during init | ~1G (reclaimed) |
| Temp during deploy | ~3G (reclaimed) |
| **Steady state** | ~818G (94%) |
| **Free** | 60G (6.5%) |

---

## Runbook Commands (Copy-Paste Ready)

For air-gap users deploying to disk-constrained nodes (>85% used):

```bash
# Step 1: Configure kubelet eviction thresholds
sudo tee /etc/rancher/rke2/config.yaml > /dev/null << 'YAML'
kubelet-arg:
  - "eviction-hard=nodefs.available<2%,imagefs.available<2%,memory.available<500Mi"
  - "eviction-soft=nodefs.available<5%,imagefs.available<5%,memory.available<1Gi"
  - "eviction-soft-grace-period=nodefs.available=2m,imagefs.available=2m,memory.available=2m"
  - "eviction-minimum-reclaim=nodefs.available=500Mi,imagefs.available=500Mi,memory.available=500Mi"
  - "image-gc-high-threshold=99"
  - "image-gc-low-threshold=98"
YAML

# Step 2: Restart RKE2 (wait ~5 min for DiskPressure to clear)
sudo systemctl restart rke2-server
until kubectl get nodes &>/dev/null; do sleep 5; done
kubectl wait --for=condition=Ready node --all --timeout=300s

# Step 3: Prepare registry directory
sudo mkdir -p /var/lib/zarf-registry
sudo chown 1000:2000 /var/lib/zarf-registry

# Step 4: Pre-create 5Gi hostPath PV
kubectl apply -f - << 'PV'
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
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
PV

# Step 5: Initialize Zarf
zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi

# Step 6: Deploy packages
NODE_IP=$(kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' | awk '{print $1}')

zarf package deploy zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_WORKER_REPLICAS=4 \
  --set DASK_SPILL_DIR=/raid/dask-spill \
  --set S3_ENDPOINT=http://${NODE_IP}:9010 \
  --set S3_BUCKET=cybersec \
  --set S3_ACCESS_KEY=minioadmin \
  --set S3_SECRET_KEY=minioadmin

# If DaskCluster timeout: manually apply and re-run
# helm list -A  # find the zarf-* release in default namespace
# helm get manifest <release-name> -n default | kubectl apply -f -
# Then re-run the same zarf package deploy command

cd kubernetes-dashboard
zarf package deploy zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst --confirm
```

## Rollback

```bash
# Revert RKE2 config to defaults
sudo rm -f /etc/rancher/rke2/config.yaml
sudo systemctl restart rke2-server

# Clean Zarf
kubectl patch pvc zarf-docker-registry -n zarf -p '{"metadata":{"finalizers":null}}' 2>/dev/null
kubectl delete pvc zarf-docker-registry -n zarf --force --grace-period=0 2>/dev/null
kubectl patch pv zarf-registry-pv -p '{"metadata":{"finalizers":null}}' 2>/dev/null
kubectl delete pv zarf-registry-pv --force --grace-period=0 2>/dev/null
kubectl delete namespace zarf --force --grace-period=0 2>/dev/null
sudo rm -rf /var/lib/zarf-registry/*
```
