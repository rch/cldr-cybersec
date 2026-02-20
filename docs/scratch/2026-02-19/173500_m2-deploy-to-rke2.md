# M2 Deploy: Teardown, Rebuild, Redeploy to Local RKE2

**Date:** 2026-02-19 17:35–17:50 UTC

## Summary

Successfully deployed M2 (gRPC NavigatorEngine + PTY proxy + GhosttyTerminal) to local RKE2 cluster. Fixed two post-deploy issues: xterm.js static asset path and empty S3 credentials.

## Changes Made

### devenv.nix

1. **`zarf:image` task** — Fixed build context mismatch:
   - Removed `cd zarf/images` (build now runs from project root)
   - Changed `-f Dockerfile.cybersec-dask` to `-f zarf/images/Dockerfile.cybersec-dask`
   - Updated tag from `2024.8.0` to `2025.2.0`
   - Updated BASE_IMAGE to `ghcr.io/dask/dask:2025.2.0`

2. **`zarf:package` task** — Updated image existence check from `2024.8.0` to `2025.2.0`

3. **All 3 NODE_IP resolutions** — Fixed to take only first InternalIP with `awk '{print $1}'` (was concatenating IPv4 + IPv6 addresses, breaking S3_ENDPOINT URL). Affects `zarf:local:deploy`, `zarf:local:status`, `zarf:local:preflight`.

### zarf/manifests/panel-viz.yaml

4. **XTERM_CDN env var** — Changed from `/static/xterm` to `/xterm`. Panel's `--static-dirs xterm=...` serves at the key name (`/xterm/`), not at `/static/xterm/`.

## Post-Deploy Hot-Fixes (running cluster)

- Recreated `otel-navigator-credentials` Secret with `minioadmin` creds
- Recreated `otel-navigator-config` ConfigMap with `S3_BUCKET=cybersec`
- Patched DaskCluster CRD with correct S3_ENDPOINT and credentials
- Set `XTERM_CDN=/xterm` on otel-navigator deployment

**Root cause of empty credentials:** Running `zarf package deploy --components=ingress` re-deploys ALL components with Zarf variable defaults (empty strings). Must always pass `--set` variables on every deploy.

## Issues Encountered

| Issue | Root Cause | Resolution |
|-------|-----------|------------|
| DiskPressure taint blocked Zarf init | 93% disk usage (916G drive) | Cleared MinIO synthetic data (116G) → 79% |
| Zarf can't find image in Podman | No Docker socket for Zarf fallback | Started `podman system service` + set `DOCKER_HOST` |
| Ingress deploy failed | nginx-ingress-controller evicted during disk pressure | Re-ran deploy after controller recovered |
| S3_ENDPOINT URL invalid | JSONPath returned IPv4+IPv6 space-separated | Added `awk '{print $1}'` to take first IP only |
| "Terminal library not loaded" | `XTERM_CDN=/static/xterm` but Panel serves at `/xterm/` | Fixed to `XTERM_CDN=/xterm` |
| "Unable to locate credentials" | Second `zarf deploy` wiped Secret to empty defaults | Recreated Secret + ConfigMap + DaskCluster CRD patch |

## Final State

All pods running:
```
dask-operator    dask-kubernetes-operator   1/1
dask             4x workers                 1/1
dask             scheduler                  1/1
jupyterhub       hub                        1/1
jupyterhub       proxy                      1/1
panel-viz        otel-navigator             2/2  (main + pty-proxy sidecar)
panel-viz        navigator-engine           1/1
k8s-dashboard    kubernetes-dashboard       1/1
```

All endpoints verified:
- Panel-Viz: `http://192.168.1.55:30506/otel-navigator` (200)
- xterm.js: `http://192.168.1.55:30506/xterm/lib/xterm.js` (200)
- Dask Dashboard: `http://192.168.1.55:30087/` (301)
- JupyterHub: `http://192.168.1.55:30080/` (302)
- K8s Dashboard: `https://192.168.1.55:10443/` (200)
- Iceberg Browser: `http://192.168.1.55:5050/` (200)
- NavigatorEngine gRPC: listening on `[::]:50051`
- PTY proxy WebSocket: sidecar running in otel-navigator pod

## Disk-Light Notes

- Cleared MinIO data (116G synthetic CloudTrail events) to resolve disk pressure
- Removed old Zarf packages (v1.0.0 + v1.1.0 = 2.5G)
- Pruned old Podman images (2024.8.0 = 1.7G)
- `~/.cache/huggingface` (945G) is on `/raid/`, NOT system drive
- Disk: 79% (192G free) after cleanup
