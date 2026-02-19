# Release Docs Update: Disk-Light as Primary Deploy Path

## Changes to `zarf/README.md`

### Structure Reorganization
- **"Quickstart"** renamed to **"Acquire Artifacts"** (download-only section)
- New **"Deploy: Disk-Light"** section is now the primary recommended path
- Old quickstart PV-backed deploy moved to **"Deploy: Full Storage"** section
- K8s Dashboard package added to artifact table and all deploy flows

### New Content: RKE2 Kubelet Eviction Thresholds (Step 0)

The critical missing piece: kubelet's default `nodefs.available<15%` threshold
applies a `node.kubernetes.io/disk-pressure:NoSchedule` taint that blocks ALL
pod scheduling — including Zarf's own registry pod. This causes `zarf init` to
hang at "performing Helm upgrade" with no clear error.

**Fix**: Lower eviction thresholds in `/etc/rancher/rke2/config.yaml`:
```yaml
kubelet-arg:
  - "eviction-hard=nodefs.available<5%,imagefs.available<5%,memory.available<100Mi"
  - "eviction-soft=nodefs.available<8%,imagefs.available<8%,memory.available<200Mi"
  - "eviction-soft-grace-period=nodefs.available=2m,imagefs.available=2m,memory.available=1m"
```

### Updated Sections
- **Verify Deployment**: Added K8s dashboard, Panel-Viz to expected pods and service table
- **Troubleshooting > Disk pressure taint**: Expanded with both immediate (taint removal) and permanent (eviction threshold) fixes, cross-references Step 0
- **Directory Structure**: Added `kubernetes-dashboard/` package and additional scripts

### Disk-Light Comparison Table
Added a clear table showing what changes between normal and disk-light modes:
- Zarf registry: 20 Gi hostPath PV vs emptyDir
- Dask spill: hostPath vs emptyDir (512Mi)
- JupyterHub pkg: unbounded vs 256Mi
- Storage provisioning: `setup_storage()` vs skipped
