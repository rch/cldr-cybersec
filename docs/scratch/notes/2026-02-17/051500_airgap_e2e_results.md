# Air-Gap E2E Zarf Deployment Results

## Status: Core Dask Stack Deployed Successfully

Date: 2026-02-17
Environment: AWS us-west-1, RKE2 v1.34.3, 1 control-plane + 8 workers
Zarf: v0.66.0, Package: cybersec-dask-amd64-1.1.0.tar.zst

## What Worked

- **Air-gap isolation**: NAT gateway removed, SG egress restricted to VPC CIDR + S3 prefix list. `curl google.com` from control plane returns connection timeout.
- **Cloudflare tunnel via bastion**: cloudflared on bastion routes to NodePorts on private subnet nodes (30087=Dask, 30080=JupyterHub, 30506=Panel-Viz, 6443=K8s API).
- **Zarf init**: All 5 components deployed (injector, seed-registry, registry, agent, git-server).
- **Zarf app deploy**: Dask operator + 4-worker DaskCluster running, all pods healthy.
- **Registry**: Self-hosted docker registry at NodePort 31999 with auth.

## Issues Found and Fixed

### 1. Registry hostPath permission denied
**Root cause**: Registry container runs as UID 1000 with `readOnlyRootFilesystem: true`. The PV hostPath directory at `/var/lib/zarf-registry` was created on the control plane but the registry pod was scheduled on worker-2 where the directory didn't exist.
**Fix**: Create `/var/lib/zarf-registry` on ALL nodes with `chown 1000:2000` and `chmod 0777`. Add `nodeAffinity` to PV to pin registry to control-plane-1. Set SELinux context to `container_file_t`.

### 2. Gitea PVC unbound
**Root cause**: No PV existed for the `data-zarf-gitea-0` PVC. Zarf expects a StorageClass provisioner to dynamically create PVs, but bare RKE2 has no default provisioner.
**Fix**: Create a hostPath PV with `claimRef` pre-bound to the PVC and `nodeAffinity` to pin to control-plane-1.

### 3. Image path mismatch (library/ prefix)
**Root cause**: The DaskCluster manifest used short image name `cybersec-dask:2025.2.0`. The Zarf agent webhook normalizes this to Docker Hub format `library/cybersec-dask:2025.2.0`, then rewrites to `127.0.0.1:31999/library/cybersec-dask:TAG`. But Zarf pushed the image as `cybersec-dask:TAG` (without `library/` prefix), so the pod couldn't find it.
**Fix for current deploy**: Copy image in registry from `cybersec-dask` to `library/cybersec-dask` using `zarf tools registry copy`.
**Fix for future builds**: Changed manifest to use `localhost:5555/cybersec-dask:2025.2.0` (full registry path). The Zarf agent then rewrites `localhost:5555/cybersec-dask` to `127.0.0.1:31999/cybersec-dask` without adding `library/`.

### 4. Wait label selector mismatch
**Root cause**: zarf.yaml `wait` action used `app.kubernetes.io/name=dask-scheduler` but the Dask operator creates pods with `app.kubernetes.io/name=cybersec-dask` + `dask.org/component=scheduler`.
**Fix**: Changed wait selector to `dask.org/component=scheduler`.

### 5. Nix-linked binary incompatible with Amazon Linux
**Root cause**: Local `zarf` binary from Nix store uses `/nix/store/*/ld-linux-x86-64.so.2` interpreter. Amazon Linux doesn't have Nix runtime.
**Fix**: Download official statically-linked release from GitHub. Added `stat` + `get_url` fallback task in `stage.yml`.

## Files Modified

| File | Change |
|------|--------|
| `zarf/manifests/dask-cluster.yaml` | Image refs: `cybersec-dask` -> `localhost:5555/cybersec-dask` |
| `zarf/manifests/jupyterhub-values.yaml` | Image ref: `cybersec-dask` -> `localhost:5555/cybersec-dask` |
| `zarf/manifests/panel-viz.yaml` | Image ref: `cybersec-dask` -> `localhost:5555/cybersec-dask` |
| `zarf/zarf.yaml` | Wait selector: `app.kubernetes.io/name=dask-scheduler` -> `dask.org/component=scheduler` |
| `infra/aws/ansible/roles/zarf-deploy/defaults/main.yml` | Fixed `zarf_local_dir` path depth |
| `infra/aws/ansible/roles/zarf-deploy/tasks/stage.yml` | Added binary download fallback |
| `infra/aws/ansible/roles/zarf-deploy/tasks/init.yml` | kubectl wait excludes Completed pods |
| `infra/aws/ansible/inventory/hosts` | Updated bastion IP |

## Remaining Work

1. **Rebuild zarf package** with fixed manifests (image refs + wait labels)
2. **Add PV provisioning** to `init.yml` — create hostPath PVs for both registry and gitea with `nodeAffinity` to control-plane
3. **Create `/var/lib/zarf-registry`** on all nodes in a pre-init task
4. **JupyterHub + Panel-Viz** — not deployed because `zarf package deploy` fails at dask-cluster wait. After fixing wait label, re-deploy will proceed past dask-cluster to remaining components.
5. **StorageClass** — JupyterHub needs dynamic PV provisioning for user notebooks. Either deploy a local-path provisioner or pre-create PVs.
