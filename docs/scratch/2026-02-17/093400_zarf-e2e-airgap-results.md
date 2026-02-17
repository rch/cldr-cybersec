# Zarf Air-Gap E2E Deployment Results

**Date:** 2026-02-17
**Package:** `zarf-package-cybersec-dask-amd64-1.1.0.tar.zst` (1.3 GB)
**Environment:** AWS us-west-1, air-gap mode (no NAT gateway, cluster nodes have no internet)
**Routing:** Developer -> Cloudflare Edge -> bastion cloudflared -> control-plane:NodePort

## Final Status: ALL SERVICES RUNNING

| Component | Namespace | Pods | Status | NodePort |
|-----------|-----------|------|--------|----------|
| Dask Operator | dask-operator | 1/1 | Running | - |
| Dask Scheduler | dask | 1/1 | Running | 30087 (dashboard), 30086 (scheduler) |
| Dask Workers (4x) | dask | 4/4 | Running | - |
| JupyterHub Hub | jupyterhub | 1/1 | Running | 30080 |
| JupyterHub Proxy | jupyterhub | 1/1 | Running | - |
| Panel-Viz (OTEL Navigator) | panel-viz | 1/1 | Running | 30506 |
| Sample Notebooks | jupyterhub | ConfigMap | Applied | - |

## External Access (Cloudflare Tunnel)

| Service | URL | Status |
|---------|-----|--------|
| Dask Dashboard | https://dask.dev.aws.zndx.org | 200 (CF Access gate) |
| JupyterHub | https://jupyter.dev.aws.zndx.org | 200 (CF Access gate) |
| Panel Viz | https://viz.dev.aws.zndx.org | 200 (CF Access gate) |

## Issues Found and Fixed

### Issue 1: Registry PV not pre-created (from prior session)
**Symptom:** `zarf init` registry pod stuck Pending - hostPath directory doesn't exist
**Fix:** Create `/var/lib/zarf-registry` with UID 1000:2000, mode 0777, SELinux `container_file_t`
**Status:** Fixed in Ansible `init.yml`

### Issue 2: Gitea PV not pre-created (from prior session)
**Symptom:** Gitea pod stuck Pending - no PV for `data-zarf-gitea-0` PVC
**Fix:** Create PV with hostPath, claimRef pre-binding, nodeAffinity to control-plane
**Status:** Fixed in Ansible `init.yml`

### Issue 3: Image ref `library/` prefix mismatch (from prior session)
**Symptom:** Zarf agent rewrites `localhost:5555/cybersec-dask` -> `127.0.0.1:31999/library/cybersec-dask` but image pushed without `library/` prefix
**Fix:** Ensure all manifests use `localhost:5555/cybersec-dask:2025.2.0` consistently
**Status:** Fixed in all manifests

### Issue 4: Dask wait label mismatch (from prior session)
**Symptom:** `app.kubernetes.io/name=dask-scheduler` doesn't match operator-created pods
**Fix:** Changed to `dask.org/component=scheduler`
**Status:** Fixed in `zarf.yaml`

### Issue 5: JupyterHub extraVolumes schema location
**Symptom:** `singleuser: Additional property extraVolumeMounts is not allowed`
**Fix:** Move `extraVolumes`/`extraVolumeMounts` from `singleuser` to `singleuser.storage` (Z2JH 4.0.0 schema)
**Status:** Fixed in `jupyterhub-values.yaml`

### Issue 6: JupyterHub storage type requires StorageClass
**Symptom:** `hub-db-dir` PVC stuck Pending - `storage.type: dynamic` needs StorageClass provisioner
**Fix:** Changed to `storage.type: none` (emptyDir) for air-gap, manual PV for hub-db
**Status:** Fixed in `jupyterhub-values.yaml` and Ansible `init.yml`

### Issue 7: JupyterHub wait label mismatch
**Symptom:** `app.kubernetes.io/component=hub` doesn't match Z2JH 4.0.0 pod labels
**Fix:** Changed to `component=hub`
**Status:** Fixed in `zarf.yaml`

### Issue 8: Panel-Viz wait label mismatch
**Symptom:** `app=panel-viz` doesn't match manifest label `app=otel-navigator`
**Fix:** Changed to `app=otel-navigator`
**Status:** Fixed in `zarf.yaml`

### Issue 9: Panel-Viz HTTP probes block on S3 timeout
**Symptom:** Readiness/liveness probes to `/otel-navigator` timeout (60s) because Panel app tries to load data from S3 (`cybersec-dask-data.s3.amazonaws.com`) on each HTTP request, which blocks indefinitely in air-gap
**Fix:** Switch from HTTP probes to TCP socket probes on port 5006
**Status:** Fixed in `panel-viz.yaml`

### Issue 10: SSH IP changed mid-session
**Symptom:** SSH to bastion timed out - developer's public IP changed
**Fix:** Added new IP to bastion security group ingress rule
**Note:** Consider using WARP CIDR range (`100.96.0.0/12`) as primary, or a wider CIDR

## Ansible Role Updates

The `zarf-deploy` role (`infra/aws/ansible/roles/zarf-deploy/`) was updated with all lessons learned:

### `tasks/init.yml` additions:
- Control plane node name discovery for PV nodeAffinity
- Registry hostPath: `/var/lib/zarf-registry` (UID 1000:2000, mode 0777, SELinux)
- Gitea hostPath: `/var/lib/zarf-gitea` (UID 1000:1000, mode 0777, SELinux)
- JupyterHub hostPath: `/var/lib/jupyterhub-db` (UID 1000:1000, mode 0777, SELinux)
- Three PVs with `claimRef` pre-binding + `nodeAffinity` to control-plane
- Post-init fixup: `create-read-only-gitea-user`, `create-artifact-registry-token`

### `defaults/main.yml` additions:
- `zarf_gitea_path`, `zarf_gitea_size`
- `zarf_jupyterhub_db_path`, `zarf_jupyterhub_db_size`

## Package Build Notes

- Requires Podman socket: `podman system service --time=0 unix:///tmp/podman.sock &`
- Build command: `DOCKER_HOST=unix:///tmp/podman.sock zarf package create . --confirm --skip-sbom`
- Build time: ~15 minutes (image pull from local Podman)
- Package size: 1.3 GB compressed

## Deployment Timing

| Phase | Duration |
|-------|----------|
| Image push (4 images) | ~2s (registry already had most images) |
| Dask operator Helm upgrade | ~1s |
| Dask cluster deploy + wait | ~2s |
| JupyterHub Helm upgrade + wait | ~3s |
| Panel-Viz manifest apply | ~1s (with TCP probes) |
| **Total (re-deploy)** | **~10s** |
| **Total (fresh deploy, estimated)** | **~5-10 min** |
