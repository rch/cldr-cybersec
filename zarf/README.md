# Cybersec Dask Air-Gap Deployment

Zarf package for deploying Dask + JupyterHub + Panel-Viz to air-gapped RKE2.

**Current release**: [`v1.1.1`](https://github.com/rch/cldr-cybersec/releases/tag/v1.1.1)

---

## Package Contents

| Component | Version | Description |
|-----------|---------|-------------|
| Dask Operator | 2024.1.0 | Manages DaskCluster CRDs |
| Dask Cluster | 2025.2.0 | Scheduler + workers with spill-to-disk |
| JupyterHub | 4.0.0 | Interactive notebooks, Dask-connected |
| Panel-Viz | 2025.2.0 | OTEL heatmap with smart windowing |
| Sample Notebooks | 2 | OTEL Data Generator, S3 Validation |

Container images baked into the `.tar.zst` (~1.3 GB total):

- `cybersec-dask:2025.2.0` — Dask + Panel + HoloViews + Datashader + s3fs
- `ghcr.io/dask/dask-kubernetes-operator:2024.1.0`
- `quay.io/jupyterhub/k8s-hub:4.0.0`
- `quay.io/jupyterhub/configurable-http-proxy:4.5.5`

```mermaid
graph LR
    subgraph "Air-Gapped RKE2 Node"
        REG[Zarf Registry :31999]
        SCHED[Scheduler :30086/:30087]
        WORK[Workers ×N]
        JH[JupyterHub :30080]
        PV[Panel-Viz]
        NB[Sample Notebooks]
        SCHED --- WORK
        JH -. Dask client .-> SCHED
        JH --- NB
    end
    REG -.-> SCHED & WORK & JH & PV
```

---

## Deploy Variables

```bash
zarf package deploy zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set S3_ENDPOINT=http://minio:9000 \
  --set S3_ACCESS_KEY=<key> \
  --set S3_SECRET_KEY=<secret> \
  --set DASK_SPILL_DIR=/mnt/nfs/dask-spill \
  --set DASK_WORKER_REPLICAS=4
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DASK_WORKER_REPLICAS` | `4` | Number of Dask workers |
| `DASK_SPILL_DIR` | `/tmp/dask-spill` | Host path for worker spill-to-disk (NFS recommended) |
| `S3_ENDPOINT` | _(empty)_ | S3-compatible endpoint URL |
| `S3_ACCESS_KEY` | _(empty)_ | S3 access key (sensitive) |
| `S3_SECRET_KEY` | _(empty)_ | S3 secret key (sensitive) |
| `INGRESS_CLASS` | `traefik` | Ingress controller class |
| `INGRESS_DOMAIN` | `cybersec.local` | Base domain for ingress |

---

## Acquire Artifacts (Internet-Connected Machine)

| Artifact | How to get | Size |
|----------|-----------|------|
| `zarf` binary | [Zarf releases](https://github.com/zarf-dev/zarf/releases) (Linux amd64) | ~100 MB |
| `zarf-init-amd64-v0.66.0.tar.zst` | `zarf tools download-init` | ~300 MB |
| `zarf-package-cybersec-dask-amd64-1.1.1.tar.zst` | [GitHub Releases](https://github.com/rch/cldr-cybersec/releases/tag/v1.1.1) | ~1.3 GB |
| `zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst` | Same | ~30 MB |

```bash
# Download on a machine with internet access
curl -LO https://github.com/zarf-dev/zarf/releases/download/v0.66.0/zarf_v0.66.0_Linux_amd64
mv zarf_v0.66.0_Linux_amd64 zarf && chmod +x zarf
./zarf tools download-init
# Download cybersec-dask and k8s-dashboard packages from GitHub Releases
```

Transfer all files to the air-gapped node (USB, SCP, data diode, etc.).

---

## Deploy: Disk-Light (Recommended for Single-Node RKE2)

This is the recommended deployment path for air-gapped RKE2 nodes, especially
when disk is constrained. It eliminates PersistentVolume requirements entirely
by using `emptyDir` for both the Zarf registry and Dask spill volumes.

### Step 0: RKE2 Kubelet Eviction Thresholds

**Before deploying**, ensure the RKE2 kubelet won't block pod scheduling due
to disk pressure. By default, kubelet taints the node with
`node.kubernetes.io/disk-pressure:NoSchedule` when `nodefs.available` drops
below 15% — this prevents Zarf's own pods from scheduling and causes
`zarf init` to hang indefinitely.

Edit `/etc/rancher/rke2/config.yaml` and add:

```yaml
kubelet-arg:
  - "eviction-hard=nodefs.available<5%,imagefs.available<5%,memory.available<100Mi"
  - "eviction-soft=nodefs.available<8%,imagefs.available<8%,memory.available<200Mi"
  - "eviction-soft-grace-period=nodefs.available=2m,imagefs.available=2m,memory.available=1m"
```

Then restart RKE2:

```bash
sudo systemctl restart rke2-server
# Wait for node Ready
sudo /var/lib/rancher/rke2/bin/kubectl \
  --kubeconfig /etc/rancher/rke2/rke2.yaml \
  wait --for=condition=Ready node --all --timeout=300s
```

If the node already has a DiskPressure taint from a previous boot, remove it:

```bash
sudo /var/lib/rancher/rke2/bin/kubectl \
  --kubeconfig /etc/rancher/rke2/rke2.yaml \
  taint nodes --all node.kubernetes.io/disk-pressure-
```

> **Why this matters**: `zarf init` bootstraps by injecting a seed registry
> into an existing kube-system pod. If kubelet won't schedule new pods (due
> to DiskPressure), the registry pod stays `Pending` forever and init hangs
> at "performing Helm upgrade". The lowered thresholds give disk-light mode
> room to operate.

### Step 1: Deploy with the Verify Script

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
export PATH=$PATH:/var/lib/rancher/rke2/bin
sudo cp zarf /usr/local/bin/ && sudo chmod +x /usr/local/bin/zarf

cd /path/to/zarf   # directory containing zarf.yaml and packages
sudo ./scripts/verify-zarf-deployment.sh --skip-build --disk-light
```

The script handles all deployment steps automatically:

1. Validates prerequisites (RKE2, kubectl, Zarf CLI)
2. Verifies kube-system injector pods are running
3. **Skips** storage provisioning (no hostPath PV)
4. Runs `zarf init --confirm --set REGISTRY_PVC_ENABLED=false` (emptyDir registry)
5. Deploys `zarf-package-cybersec-dask-*.tar.zst`
6. Patches DaskCluster spill volume from hostPath to `emptyDir` (512Mi)
7. Fixes any image tag mismatches (Zarf suffix drift)
8. Verifies all pods are Running

**Auto-detection**: Even without `--disk-light`, the script auto-enables it
when `df /var/lib/rancher` shows <10% free or a `node.kubernetes.io/disk-pressure`
taint is detected.

### Step 2: Deploy the Kubernetes Dashboard

```bash
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf package deploy \
  zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst --confirm
```

### Step 3: Access Services

```bash
# Dashboard token (copy the output)
zarf connect kubernetes-dashboard

# Or access services directly via NodePort:
```

| Service | URL | Credentials |
|---------|-----|-------------|
| Dask Dashboard | `http://<node>:30087` | — |
| Dask Scheduler | `tcp://<node>:30086` | — |
| JupyterHub | `http://<node>:30080` | admin / changeme |
| K8s Dashboard | `https://<node>:30443` | token from `zarf connect` |

### What Disk-Light Changes

| Aspect | Normal Mode | Disk-Light Mode |
|--------|-------------|-----------------|
| Zarf registry | 20 Gi hostPath PV | `emptyDir` (no PV) |
| Dask spill volume | hostPath to `DASK_SPILL_DIR` | `emptyDir` (512Mi) |
| JupyterHub pkg volume | `emptyDir` (unbounded) | `emptyDir` (256Mi) |
| Worker replicas | configurable | defaults to 4 |
| Storage provisioning | `setup_storage()` creates PV | skipped |

> **Trade-off**: The emptyDir registry is ephemeral — data is lost on pod
> restart. This is acceptable because `zarf package deploy` re-pushes all
> images automatically. For production with ample disk, use the full-storage
> deployment below.

### Manual Disk-Light Deploy (Without the Script)

If you prefer to run the steps yourself:

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml

# 1. Init Zarf without registry PVC
sudo zarf init --confirm --set REGISTRY_PVC_ENABLED=false

# 2. Deploy the Dask stack
sudo zarf package deploy \
  zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_WORKER_REPLICAS=4

# 3. Patch spill volume to emptyDir
sudo kubectl patch daskcluster cybersec-dask -n dask --type=json -p \
  '[{"op":"replace","path":"/spec/worker/spec/volumes/0","value":{"name":"dask-spill","emptyDir":{"sizeLimit":"512Mi"}}}]'
sudo kubectl delete pods -n dask -l dask.org/component=worker

# 4. Deploy K8s Dashboard
sudo zarf package deploy \
  zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst --confirm

# 5. Verify
sudo kubectl get pods -A
```

---

## Deploy: Full Storage (Ample Disk)

For nodes with sufficient disk (100+ GB free), use the traditional PV-backed approach:

```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
export PATH=$PATH:/var/lib/rancher/rke2/bin
sudo cp zarf /usr/local/bin/ && sudo chmod +x /usr/local/bin/zarf

# 1. Provision storage for Zarf's internal registry
sudo mkdir -p /var/lib/zarf-registry && sudo chmod 777 /var/lib/zarf-registry
sudo kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: 20Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF

# 2. Initialize Zarf
sudo zarf init --confirm

# 3. Deploy Dask stack
sudo zarf package deploy \
  zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_SPILL_DIR=/mnt/nfs/dask-spill   # or /tmp/dask-spill

# 4. Deploy K8s Dashboard
sudo zarf package deploy \
  zarf-package-cybersec-k8s-dashboard-amd64-2.7.0.tar.zst --confirm

# 5. Access dashboard
zarf connect kubernetes-dashboard
```

---

## Build from Source

Required when modifying images, manifests, or notebooks.

```bash
# 1. Build cybersec-dask image
cd zarf/images
podman build -t localhost:5555/cybersec-dask:2025.2.0 -f Dockerfile.cybersec-dask .

# 2. Ensure podman socket is running (Zarf uses Docker API)
mkdir -p /run/user/$(id -u)/podman
podman system service --time=600 unix:///run/user/$(id -u)/podman/podman.sock &

# 3. Create package
cd /path/to/cybersec/zarf
DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock \
  zarf package create . --confirm --skip-sbom

# Output: zarf-package-cybersec-dask-amd64-1.1.1.tar.zst (~1.3 GB)
```

Upstream images (`dask-kubernetes-operator`, `k8s-hub`, `configurable-http-proxy`) are pulled automatically during `zarf package create`.

---

## RKE2 Installation (Fresh Node)

Skip this section if RKE2 is already running.

### Additional artifacts (beyond those in Quickstart)

| Artifact | Source |
|----------|--------|
| `rke2-images.linux-amd64.tar.zst` | [RKE2 releases](https://github.com/rancher/rke2/releases) |
| `rke2.linux-amd64.tar.gz` | Same |
| `install.sh` | `curl -sfL https://get.rke2.io` |

### Install

```bash
sudo mkdir -p /var/lib/rancher/rke2/agent/images/
sudo cp rke2-images.linux-amd64.tar.zst /var/lib/rancher/rke2/agent/images/
sudo INSTALL_RKE2_ARTIFACT_PATH=. sh install.sh
sudo systemctl enable --now rke2-server
# Wait for "Running kube-apiserver" in:  sudo journalctl -u rke2-server -f
```

### Node Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 cores | 8 cores |
| RAM | 16 GB | 32 GB |
| Disk | 100 GB | 200 GB |
| OS | RHEL 8/9, Rocky 8/9, Ubuntu 22.04+ | |

---

## Verify Deployment

```bash
# All pods
sudo kubectl get pods -A

# Expected:
#   zarf/                   zarf-docker-registry-*           1/1  Running
#   dask-operator/          dask-kubernetes-operator-*       1/1  Running
#   dask/                   cybersec-dask-scheduler-*        1/1  Running
#   dask/                   cybersec-dask-default-worker-*   1/1  Running  (×N)
#   jupyterhub/             hub-*                            1/1  Running
#   jupyterhub/             proxy-*                          1/1  Running
#   panel-viz/              panel-viz-*                      1/1  Running
#   kubernetes-dashboard/   kubernetes-dashboard-*           1/1  Running

# Dask cluster health
sudo kubectl get daskcluster -n dask

# Scheduler HTTP health (should return 200)
curl -sf http://127.0.0.1:30087/health && echo OK

# Re-run the verify step only (skips init and build)
sudo ./scripts/verify-zarf-deployment.sh --skip-init --skip-build

# Dashboard access token
zarf connect kubernetes-dashboard
```

### Access Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Dask Dashboard | `http://<node>:30087` | — |
| Dask Scheduler | `tcp://<node>:30086` | — |
| JupyterHub | `http://<node>:30080` | admin / changeme |
| K8s Dashboard | `https://<node>:30443` | token from `zarf connect` |
| Panel-Viz | `http://<node>:30506` | — |
| Sample Notebooks | `/app/sample-notebooks/` | (inside JupyterLab) |

---

## Spill-to-Disk Configuration

Workers use `--local-directory /dask-spill` to spill intermediate data under memory pressure. The `DASK_SPILL_DIR` variable maps a host path into the container.

| Scenario | Setting |
|----------|---------|
| Testing / ephemeral | `/tmp/dask-spill` (default, auto-created) |
| Production / NFS | `/mnt/nfs/dask-spill` (set at deploy time) |
| Shared storage | Any path visible to all workers on the node |

The hostPath uses `DirectoryOrCreate` — no pre-provisioning needed.

To change after deployment:
```bash
sudo kubectl edit daskcluster cybersec-dask -n dask
# Update spec.worker.spec.volumes[0].hostPath.path
# Then restart workers:
sudo kubectl rollout restart deployment -n dask -l dask.org/component=worker
```

---

## Scaling

```bash
# Scale workers (immediate)
sudo kubectl patch daskcluster cybersec-dask -n dask --type=merge \
  -p '{"spec":{"worker":{"replicas":2}}}'

# Or set at deploy time
zarf package deploy ... --set DASK_WORKER_REPLICAS=8 --confirm
```

---

## Troubleshooting

### Registry PVC won't bind (no StorageClass provisioner)

Bare RKE2 without Rancher has no default StorageClass. The Zarf internal
registry requests a 20 Gi PVC which will stay `Pending` indefinitely.

**Option A — Disable PVC entirely (simplest, data in emptyDir):**

Registry data is lost on pod restart, but `zarf package deploy` re-pushes
images automatically. Best for resource-constrained nodes.

```bash
# Clean any previous failed init
sudo zarf package remove --confirm 2>/dev/null; true
sudo kubectl delete pvc -n zarf zarf-docker-registry --force --grace-period=0 2>/dev/null; true
sudo kubectl delete pv -l app=zarf-registry --force --grace-period=0 2>/dev/null; true

# Init with PVC disabled
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml \
  zarf init --confirm --set REGISTRY_PVC_ENABLED=false
```

**Option B — Smaller PVC (disk-constrained nodes):**

The default 20 Gi PVC is a label, not a reservation — actual usage is ~2 GB
for this package. But the PVC request must match an available PV.

```bash
# Create PV with reduced capacity
sudo kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  storageClassName: ""
  capacity:
    storage: 5Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
EOF

# Init with matching PVC size
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml \
  zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi
```

**Option C — Pre-bound PV (recommended for production):**

Pre-create the PV with a `claimRef` so it binds immediately when the PVC
is created during init. This is the approach used in the Quickstart.

```bash
sudo kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolume
metadata:
  name: zarf-registry-pv
spec:
  capacity:
    storage: 20Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /var/lib/zarf-registry
    type: DirectoryOrCreate
  claimRef:
    namespace: zarf
    name: zarf-docker-registry
EOF

sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf init --confirm
```

**Registry init variables** (all passed via `--set KEY=value`):

| Variable | Default | Description |
|----------|---------|-------------|
| `REGISTRY_PVC_ENABLED` | `true` | Set `false` to use emptyDir instead of PVC |
| `REGISTRY_PVC_SIZE` | `20Gi` | PVC storage request size |
| `REGISTRY_EXISTING_PVC` | _(empty)_ | Name of a pre-existing PVC to use |
| `--storage-class` | _(flag)_ | StorageClass for registry and git server |

### Registry PVC stuck in Terminating

PVCs and PVs with finalizers can hang on delete. Force removal:

```bash
# Remove finalizers, then delete
sudo kubectl patch pvc zarf-docker-registry -n zarf -p '{"metadata":{"finalizers":null}}'
sudo kubectl delete pvc zarf-docker-registry -n zarf --force --grace-period=0

sudo kubectl patch pv zarf-registry-pv -p '{"metadata":{"finalizers":null}}'
sudo kubectl delete pv zarf-registry-pv --force --grace-period=0
```

### Registry push fails with "Filesystem" error (NFS)

The Docker registry uses hard links and atomic renames that NFS does not
support. **Do not use NFS for the registry PV.** Use local disk (HostPath).

NFS is fine for:
- Dask spill-to-disk (`DASK_SPILL_DIR`)
- User data / notebook storage

### `zarf init` hangs at "performing Helm upgrade"

The Helm upgrade waits for the registry pod to become Ready. Check why
the pod is stuck:

```bash
KUBECTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

# Pod status
$KUBECTL get pods -n zarf

# Why it's not scheduling
$KUBECTL describe pods -n zarf | grep -A 5 -E "Events:|Warning"

# PVC binding
$KUBECTL get pvc -n zarf
$KUBECTL get pv
```

| Pod status | Probable cause | Fix |
|-----------|----------------|-----|
| `Pending` | Unbound PVC | See "Registry PVC won't bind" above |
| `Pending` | Insufficient resources | Free memory or reduce resource requests |
| `ContainerCreating` | Image pull from seed registry slow | Wait, or increase `--timeout 15m` |
| `CrashLoopBackOff` | Disk full or OOM | Check `df -h` and `free -h` |

### `zarf init` fails with "cannot patch PersistentVolumeClaim"

This occurs when re-running init after a failed attempt left a PVC with
a different size. PVC storage requests are immutable.

```bash
# Remove old state
sudo zarf package remove --confirm 2>/dev/null; true
sudo kubectl delete pvc -n zarf zarf-docker-registry --force --grace-period=0
sudo kubectl patch pvc zarf-docker-registry -n zarf -p '{"metadata":{"finalizers":null}}' 2>/dev/null
# Then re-run init
```

### ImagePullBackOff (Zarf suffix mismatch)

Zarf rewrites image tags with a suffix derived from the init package. If the init and application packages were built at different times, suffixes won't match.

```bash
# What pods expect:
sudo kubectl get events -n dask | grep "pulling image"

# What registry has:
REG_PASS=$(sudo kubectl get secret -n zarf zarf-state -o jsonpath='{.data.state}' \
  | base64 -d | jq -r '.registryInfo.pullPassword')
curl -s -u "zarf-pull:$REG_PASS" http://127.0.0.1:31999/v2/_catalog

# Fix: re-tag the image to match the expected suffix
podman login 127.0.0.1:31999 -u zarf-push -p "$PUSH_PASS" --tls-verify=false
podman pull 127.0.0.1:31999/cybersec-dask:2025.2.0 --tls-verify=false
podman tag  127.0.0.1:31999/cybersec-dask:2025.2.0 \
            127.0.0.1:31999/library/cybersec-dask:2025.2.0-zarf-<SUFFIX>
podman push 127.0.0.1:31999/library/cybersec-dask:2025.2.0-zarf-<SUFFIX> --tls-verify=false
sudo kubectl delete pods -n dask --all
```

### RKE2 won't start

```bash
sudo journalctl -u rke2-server --no-pager | tail -50

# Common fixes:
sudo systemctl stop firewalld && sudo systemctl disable firewalld
sudo setenforce 0
df -h /var/lib/rancher   # need ≥20 GB free
```

### Disk pressure taint (pods won't schedule)

The kubelet applies `node.kubernetes.io/disk-pressure:NoSchedule` when
`nodefs.available` drops below the eviction threshold (default 15%).
This blocks ALL new pods, including Zarf's.

**Immediate fix** — remove the taint:

```bash
sudo kubectl taint nodes --all node.kubernetes.io/disk-pressure-
```

**Permanent fix** — lower the eviction thresholds in `/etc/rancher/rke2/config.yaml`:

```yaml
kubelet-arg:
  - "eviction-hard=nodefs.available<5%,imagefs.available<5%,memory.available<100Mi"
  - "eviction-soft=nodefs.available<8%,imagefs.available<8%,memory.available<200Mi"
  - "eviction-soft-grace-period=nodefs.available=2m,imagefs.available=2m,memory.available=1m"
```

Then restart: `sudo systemctl restart rke2-server`

See **Deploy: Disk-Light > Step 0** for full details.

---

## Maintenance

```bash
# Update: transfer new package, then re-deploy
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf package deploy \
  zarf-package-cybersec-dask-amd64-X.X.X.tar.zst --confirm

# Uninstall
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf package remove cybersec-dask --confirm

# Full teardown (removes Zarf + RKE2)
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf destroy --confirm
sudo /usr/local/bin/rke2-uninstall.sh
```

---

## Directory Structure

```
zarf/
├── zarf.yaml                           # Package definition (components, variables, images)
├── charts/
│   ├── dask-kubernetes-operator-2024.1.0.tgz
│   └── jupyterhub-4.0.0.tgz
├── images/
│   ├── Dockerfile.cybersec-dask        # Custom image: Dask + Panel + Datashader + s3fs
│   ├── otel-navigator.py               # Panel-Viz app (smart windowing, embedded in image)
│   ├── requirements-airgap.txt         # Pinned Python deps for reproducible builds
│   └── sample-notebooks/               # Stripped notebooks baked into image at /app/
├── manifests/
│   ├── dask-cluster.yaml               # DaskCluster CRD (scheduler + workers + spill volume)
│   ├── dask-operator-values.yaml       # Operator Helm values
│   ├── jupyterhub-values.yaml          # Hub + proxy + singleuser + notebook mounts
│   ├── panel-viz.yaml                  # Panel deployment + service
│   ├── sample-notebooks-configmap.yaml # Notebooks injected as ConfigMap
│   ├── namespace.yaml                  # dask namespace
│   ├── jupyterhub-namespace.yaml       # jupyterhub namespace
│   └── ingress.yaml                    # Traefik ingress rules
├── notebooks/
│   ├── OTEL_Data_Generator.ipynb       # Vectorized synthetic OTEL span generator
│   └── Dask_S3_Validation.ipynb        # Out-of-core Dask stress test (30 GB)
├── kubernetes-dashboard/
│   ├── zarf.yaml                       # K8s Dashboard package (v2.7.0)
│   └── manifests/dashboard.yaml        # Dashboard + metrics-scraper + RBAC
└── scripts/
    ├── embed-notebooks.py              # Strips outputs, embeds in ConfigMap YAML
    ├── verify-zarf-deployment.sh       # Full deploy + verify (--skip-build --disk-light)
    ├── validate-deployment.sh          # Lightweight post-deploy checks
    └── install-rke2-secondary.sh       # Isolated secondary RKE2 instance
```

## Tested Versions

| Component | Version |
|-----------|---------|
| RKE2 | v1.34.3+rke2r1 |
| Zarf | v0.66.0 |
| Dask | 2025.2.0 |
| Dask Operator | 2024.1.0 |
| JupyterHub | 4.0.0 |
| Panel / HoloViews / Datashader | 1.5+ / 1.20+ / 0.16+ |
