# Zarf Local E2E Deployment - Verified

## Summary

Completed full E2E deployment of the `zarf:local:*` task group to local RKE2 cluster.
All services deployed and accessible. Fixed post-deploy bugs in the devenv tasks.

## E2E Deployment Sequence

1. **Preflight** (`zarf:local:preflight`): PASSED
2. **Init** (`zarf:local:init`): PASSED (141s)
   - Installed local-path-provisioner (bare RKE2 had no StorageClass)
   - Used `REGISTRY_PVC_SIZE=1Gi` (not `REGISTRY_PVC_ENABLED=false` which breaks registry)
3. **Deploy** (`zarf:local:deploy`): All components deployed, but task exited 1 due to post-deploy bugs
4. **Status** (`zarf:local:status`): All services accessible

## Bugs Fixed

### 1. Wrong Pod Label in kubectl wait
- `app=panel-viz` → `app=otel-navigator`
- The Zarf-deployed Panel-Viz chart uses `otel-navigator` as the app label

### 2. set -euo pipefail Exit Code Capture
- `kubectl wait ...; PANEL_READY=$?` fails immediately on non-zero with `set -e`
- Fixed: `if kubectl wait ...; then PANEL_READY=0; else PANEL_READY=1; fi`

### 3. HTTP Status Code Check Too Strict
- Services return 301/302 redirects, not 200
- Fixed: Accept 200, 301, 302 as "accessible"

### 4. Wrong Service Name/Port in Port-Forward Fallback
- `svc/panel-viz` → `svc/otel-navigator`
- Port `80` → `5006` (actual container port)

## Verified Service Accessibility

| Service | URL | HTTP Code |
|---------|-----|-----------|
| Panel-Viz | http://0.0.0.0:30506/ | 302 |
| Dask Dashboard | http://localhost:30087/ | 301 |
| JupyterHub | http://localhost:30080/ | 302 |

## Cluster State

- 4 Dask workers + scheduler (all Running)
- JupyterHub hub + proxy (Running)
- Panel-Viz otel-navigator (Running)
- Zarf registry + 2 agent hooks (Running)
- local-path-provisioner (Running)
- Node: 41% memory (52 GB / 125 GB), CPU 3%

## Config Settings (.env)

```
KUBECONFIG=/home/rch/.kube/rke2.yaml
DASK_SPILL_DIR=/raid/rke2/dask-spill
DASK_WORKER_REPLICAS=4
```

## Key Learnings

- Bare RKE2 clusters have no StorageClass; must install local-path-provisioner
- Zarf registry with `REGISTRY_PVC_ENABLED=false` breaks config; use small PVC instead
- RKE2 kubeconfig at `~/.kube/rke2.yaml` may not contain "rke2" markers in content
- Always use `if cmd; then` pattern (not `cmd; $?`) when sourcing set -e scripts
