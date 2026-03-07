# Walkthrough Live Validation — 2026-03-06

## Environment
- **Node**: tinybox (192.168.1.55)
- **RKE2**: v1.34.3+rke2r3
- **Zarf**: v0.66.0
- **Package**: cybersec-dask v1.2.0 (v1.2.1 not built, v1.2.0 used)
- **Disk**: 916GB system drive, 87% used (118GB free)

## Test Flow
Full teardown-to-deploy cycle against a cluster with stale state from a previous deployment (13 days old).

## Pre-existing State
- No zarf/dask/jupyterhub/panel-viz namespaces (previously cleaned)
- 2 orphaned PVs in Released state (`zarf-registry-pv`, jupyterhub PV)
- 6 orphaned Zarf Helm release secrets in `default` namespace
- 1 zombie DaskCluster CRD instance with kopf finalizer (namespace gone, CRD stuck in etcd)
- Stale containerd images from previous deploy
- Kubeconfig user copy 16 days older than system copy

## Issues Found & Fixed

### Issue 1: Phase 3a — Interactive Package Selector
`zarf package remove --confirm` without a package name opens an interactive TUI selector.
**Fix**: Changed to `zarf package remove zarf-init --confirm`.

### Issue 2: Orphaned Helm Secrets Not Cleaned
Teardown left Zarf Helm release secrets in `default` namespace (6 secrets with `zarf-` hashed names).
**Fix**: Added Phase 3d with targeted cleanup via `awk '$1 ~ /zarf-/ {print $1}'`.

### Issue 3: Zombie DaskCluster Unreachable via kubectl
DaskCluster CRD instance persisted in etcd after namespace deletion. `kubectl delete/patch` failed with "namespace not found" because the namespace-scoped API couldn't route the request.
**Fix**: Added Phase 2d — recreate namespace temporarily, clear kopf finalizer, delete namespace. Updated Discussion C with recovery procedure.

### Issue 4: Registry Auth Required
`curl -sf http://NODE_IP:31999/v2/` returns 401 Unauthorized — the Zarf registry requires basic auth.
**Fix**: Changed to `curl -so /dev/null -w "%{http_code}"` checking for 401 (alive) vs 000 (unreachable).

### Issue 5: Hardcoded Package Version
Deploy command referenced `1.2.1` but user may have any version.
**Fix**: Changed to glob pattern `ls -t zarf-package-cybersec-dask-amd64-*.tar.zst | head -1`.

### Issue 6: JupyterHub PVC Blocked on Air-Gap
JupyterHub's `hub-db-dir` PVC uses `local-path` StorageClass. The `local-path-provisioner` pod can't pull its image in air-gap, leaving the hub stuck in Pending.
**Initial fix**: Added Phase 7a-fix with manual PV creation including `claimRef` with PVC UID.
**Root cause fix**: Changed `hub.db.type` from `sqlite-pvc` (default) to `sqlite-memory` in `jupyterhub-values.yaml`, eliminating the PVC dependency entirely. Hub state lives in memory; on pod restart users re-login but running notebook servers are unaffected. Walkthrough Phase 7a-fix replaced with 7a-note explaining the fix and referencing the manual workaround for older packages.

## Interventions Required During Deployment
1. Refreshed stale kubeconfig (Phase 0) — system copy was 16 days newer
2. Recreated namespace to clear zombie DaskCluster finalizer (Phase 2d)
3. Created manual PV for JupyterHub hub-db-dir PVC (Phase 7a-fix)
4. Installed then removed broken local-path-provisioner (attempted before manual PV approach)

## Timing
| Phase | Duration |
|-------|----------|
| Zarf init | ~43 seconds |
| Package deploy (total) | ~14 minutes |
| JupyterHub blocked on PVC | ~12 minutes (until manual PV created) |
| Full validation cycle | ~25 minutes |

## Final State
All pods Running, all HTTP endpoints responding, Dask cluster connected with 4 workers.
