# Zarf Init Failure: Disk-Light Air-Gap Fix

## Problem

On `usfwdbig01` (air-gap RKE2), `zarf init` failed with:

```
ERR failed to deploy package: unable to deploy component "zarf-seed-registry":
    unable to install chart context deadline exceeded
```

### Root cause chain

1. **Previous init left a PVC in `Lost` phase**: The PV `zarf-registry-pv` was
   deleted, but PVC `zarf-docker-registry` remained with finalizer
   `kubernetes.io/pvc-protection` blocking deletion.

2. **Helm upgrade deadlock**: The docker-registry chart references the PVC, but
   it's unusable (Lost + pending deletion). Helm upgrade times out, rollback
   also fails because it can't find the PVC.

3. **User attempted `REGISTRY_PVC_ENABLED=false`**: This is documented as a
   workaround but **crashes the registry** with `"no storage configuration
   provided"`. The Helm chart's filesystem storage driver requires a volume
   mount — there is no emptyDir fallback.

## Analysis (pre-init.png / post-init.png)

**pre-init.png** — `kubectl get pvc -n zarf -o yaml`:
- PVC `zarf-docker-registry`, `status.phase: Lost`
- `deletionTimestamp: 2026-02-18T00:16:11Z` (stuck for >24h)
- Finalizer: `kubernetes.io/pvc-protection`

**post-init.png** — `zarf init` output:
- `zarf-seed-registry` Helm upgrade → context deadline exceeded
- Rollback → `no PersistentVolumeClaim with the name "zarf-docker-registry" found`
- Next command: `./zarf init --confirm --set REGISTRY_PVC_ENABLED=false`

## Fix

### 1. Script fix: `verify-zarf-deployment.sh`

Changed disk-light mode from:
- `REGISTRY_PVC_ENABLED=false` + skip `setup_storage()`

To:
- Small hostPath PV (5 Gi) + `REGISTRY_PVC_SIZE=5Gi`
- Auto-cleanup of stuck PVCs (Lost phase detection)
- SELinux context + proper ownership (UID 1000:2000)

### 2. README fix: `zarf/README.md`

- Rewrote "Disk-Constrained Deployment" section with:
  - Prominent warning about `REGISTRY_PVC_ENABLED=false` crash
  - "What goes where" table (registry=local, spill=NFS)
  - Manual procedures for both NFS and no-NFS scenarios
  - Disk budget reference table (~7-9 GB local minimum)
- Added "Recovery from failed `zarf init`" troubleshooting section
- Struck through Option A with crash explanation

### 3. Recovery procedure for `usfwdbig01`

```bash
KUBECTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

# Clean stuck state
$KUBECTL patch pvc zarf-docker-registry -n zarf -p '{"metadata":{"finalizers":null}}'
$KUBECTL delete pvc zarf-docker-registry -n zarf --force --grace-period=0
$KUBECTL patch pv zarf-registry-pv -p '{"metadata":{"finalizers":null}}'
$KUBECTL delete pv zarf-registry-pv --force --grace-period=0
$KUBECTL delete namespace zarf --wait=false
$KUBECTL patch namespace zarf -p '{"metadata":{"finalizers":null}}'

# Pre-create small PV on local disk
sudo mkdir -p /var/lib/zarf-registry && sudo chown 1000:2000 /var/lib/zarf-registry
sudo chmod 777 /var/lib/zarf-registry
$KUBECTL apply -f - <<'EOF'
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
EOF

# Init + deploy with NFS spill
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi
sudo KUBECONFIG=/etc/rancher/rke2/rke2.yaml zarf package deploy \
  zarf-package-cybersec-dask-amd64-1.1.1.tar.zst --confirm \
  --set DASK_SPILL_DIR=/mnt/nfs/dask-spill --set DASK_WORKER_REPLICAS=4
```

## Key takeaway

The Zarf registry **always** needs real storage. On disk-constrained nodes, use
a small hostPath PV (5 Gi label, ~2 GB actual) rather than disabling PVC.
NFS is fine for Dask spill but **not** for the registry (hard links + atomic
renames).

## Files changed

- `zarf/scripts/verify-zarf-deployment.sh` — disk-light mode: small PV + auto-cleanup
- `zarf/README.md` — rewritten disk-constrained section, recovery procedure, warnings
