# Disk-Light Zarf Deployment + Housekeeping

## Problem

Deploying the cybersec-dask Zarf package to an air-gapped RKE2 node with <3% free disk
failed because:
1. NFS doesn't support Docker registry hard links/atomic renames
2. hostPath PVs require local disk space that doesn't exist

## Solution

### Housekeeping
- Moved untracked scratch files (screenshots, .tar.zst packages, tfplan outputs) to `build/scratch/`
- `build/` was already in `.gitignore`

### Disk-Light Mode
- **`zarf/zarf.yaml`**: Fixed `DASK_WORKER_REPLICAS` default from `"32"` to `"4"` (README already documented 4)
- **`zarf/manifests/jupyterhub-values.yaml`**: Added `sizeLimit: 256Mi` to `jupyterhub-pkg` emptyDir volume
- **`zarf/scripts/verify-zarf-deployment.sh`**: Added `--disk-light` flag with:
  - Auto-detection: enables when `df /var/lib/rancher` shows <10% free or DiskPressure taint detected
  - Skips `setup_storage()` (no PV/PVC creation)
  - Passes `--set REGISTRY_PVC_ENABLED=false` to `zarf init`
  - Post-deploy patches Dask spill volume from hostPath to emptyDir (512Mi)
  - Prints summary banner showing active disk-light settings
- **`cybersec/zarf/preflight.py`**: Added `_check_node_disk()` that checks for DiskPressure condition/taint via kubectl
- **`zarf/README.md`**: Added "Disk-Constrained Deployment" section after Quickstart

## Key Insight

`zarf init --set REGISTRY_PVC_ENABLED=false` uses emptyDir instead of a PVC, eliminating
the disk requirement for the Zarf registry. Combined with emptyDir-based spill volumes,
the entire stack can deploy on disk-constrained nodes.

## Files Modified

| File | Change |
|------|--------|
| `zarf/zarf.yaml` | Default `DASK_WORKER_REPLICAS`: 32 → 4 |
| `zarf/manifests/jupyterhub-values.yaml` | `sizeLimit: 256Mi` on emptyDir |
| `zarf/scripts/verify-zarf-deployment.sh` | `--disk-light` flag + auto-detect + patches |
| `cybersec/zarf/preflight.py` | `_check_node_disk()` DiskPressure check |
| `zarf/README.md` | "Disk-Constrained Deployment" section |
