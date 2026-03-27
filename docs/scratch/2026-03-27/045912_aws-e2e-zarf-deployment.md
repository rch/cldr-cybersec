# AWS E2E Deployment — Standardized on Zarf

**Date**: 2026-03-26/27 (started ~20:30 PDT, completed ~21:59 PDT)
**Branch**: `rch/devenv`
**Region**: `us-west-1`
**Zarf Package**: `v1.2.2` (1.3 GB, built from previous session)

## Decision

Standardize on Zarf as the single deployment path for both air-gap (local RKE2) and AWS environments. This replaces the Ansible-native app deployment (which used `ghcr.io/dask/dask:latest` with a simpler HoloViews panel) with the full OTEL Navigator stack including ghostty-web WASM terminal, PTY proxy, and NavigatorEngine.

## Final Deployment State

### Infrastructure
- **9 K8s nodes**: 1 control plane (`m6i.xlarge`) + 8 workers (`r6i.xlarge`, 4 vCPU / 32 GiB each)
- **RKE2 v1.34.5** on Amazon Linux 2023
- **VPC**: 10.100.0.0/16 with NAT gateway (non-airgap mode)
- **NLB**: K8s API endpoint via `cybersec-dask-k8s-api-*.elb.us-west-1.amazonaws.com:6443`
- **S3**: `cybersec-dask-00631868-data` bucket

### Application Stack (via Zarf)
| Namespace | Component | Status | Details |
|-----------|-----------|--------|---------|
| `dask-operator` | Dask Kubernetes Operator | Running | Manages DaskCluster CRD |
| `dask` | Scheduler + 16 Workers | Running | 103.1 GB total memory across 8 nodes |
| `jupyterhub` | Hub + Proxy | Running | Zero-to-JupyterHub Helm chart |
| `panel-viz` | OTEL Navigator (2/2) | Running | Bokeh server + PTY proxy sidecar |
| `panel-viz` | NavigatorEngine | Running | gRPC backend on port 50051 |
| `cloudflare-system` | Cloudflared (2/2) | Running | 4 tunnel connections to SJC edge |
| `zarf` | Registry + Agent | Running | Zarf internal registry + mutation webhook |
| `local-path-storage` | Provisioner | Running | Default StorageClass for PVCs |

### Cloudflare Zero Trust Endpoints
All behind WARP device posture (302 redirect to auth when not enrolled):
- `https://dask.dev.aws.zndx.org` — Dask Dashboard
- `https://jupyter.dev.aws.zndx.org` — JupyterHub
- `https://viz.dev.aws.zndx.org` — Panel-Viz (OTEL Navigator + WASM terminal)
- `https://k8s.dev.aws.zndx.org` — Kubernetes Dashboard
- `https://bastion.dev.aws.zndx.org` — SSH to bastion (via cloudflared)

### Tunnel Routing (Zarf service names)
```
dask.dev.aws.zndx.org    → cybersec-dask-scheduler.dask.svc.cluster.local:8787
jupyter.dev.aws.zndx.org → proxy-public.jupyterhub.svc.cluster.local:80
viz.dev.aws.zndx.org     → otel-navigator.panel-viz.svc.cluster.local:5006
k8s.dev.aws.zndx.org     → kubernetes-dashboard.kubernetes-dashboard.svc.cluster.local:443
```

## Code Changes Made

### 1. `infra/aws/tofu/cloudflare.tf`
Updated tunnel ingress rules to match Zarf service names:
- `simple-scheduler.dask.svc` → `cybersec-dask-scheduler.dask.svc` (port 8787)
- `panel-viz.panel-viz.svc:80` → `otel-navigator.panel-viz.svc:5006`

### 2. `infra/aws/ansible/roles/cloudflare-tunnel/defaults/main.yml`
Updated defaults to match Zarf:
- `dask_dashboard_service_name: cybersec-dask-scheduler`
- `panel_viz_service_name: otel-navigator`
- `panel_viz_service_port: 5006`

### 3. `infra/aws/ansible/playbooks/cluster-only.yml` (NEW)
RKE2-only playbook: common → rke2-server → rke2-agent → s3-data. Skips Dask/JupyterHub/Panel-Viz (Zarf handles those).

### 4. `devenv.nix` — `aws:deploy:zarf` task (NEW)
Orchestrates SCP + SSH to transfer and deploy the Zarf package on the control plane.

### 5. `infra/aws/tofu/terraform.tfvars`
- Changed `airgap_mode = true` → `airgap_mode = false` (NAT gateway needed for RKE2 installer)
- Updated stale SSH CIDR `216.147.122.181/32` → `216.147.124.22/32`

## Execution Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| Pre-flight + code changes | ~15 min | Plan approval + 5 file changes |
| `aws:provision` (OpenTofu) | ~4 min (256s) | VPC, EC2, NLB, S3, IAM, Cloudflare Tunnel + Zero Trust |
| Re-provision (airgap fix) | ~3 min (167s) | Just NAT gateway + SG CIDR delta |
| `aws:inventory` | ~1 min | Generated Ansible inventory |
| `cluster-only.yml` (Ansible) | ~15 min | RKE2 server + 8 agents + S3 bucket |
| Install Zarf binary on CP | ~2 min | Downloaded v0.74.0 from GitHub |
| SCP Zarf package (1.3 GB) | ~18 min | Via ProxyCommand (bastion /tmp too small) |
| Download zarf-init package | ~5 min | 390 MB from GitHub on CP |
| Install local-path-provisioner | ~2 min | RKE2 has no default StorageClass |
| `zarf init` (attempt 1: stuck) | ~41 min wasted | Registry PVC Pending, no StorageClass |
| `zarf init` (attempt 2: success) | ~5 min | After local-path-provisioner installed |
| `zarf package deploy` (attempt 1) | ~10 min | Image push timeout on 658 MB image |
| `zarf package deploy` (attempt 2) | ~15 min | Success (cached layers from attempt 1) |
| Cloudflare tunnel (Ansible) | ~5 min | Required `INGRESS_PROVIDER=cloudflare` + namespace label |
| Verification | ~10 min | All services confirmed healthy |
| **Total wall clock** | **~2.5 hours** | ~1 hour was wasted on interventions |

## Direct Interventions (Tech Debt)

### Critical (blocks automation)

1. **`airgap_mode = true` in terraform.tfvars**
   - **Impact**: Removed NAT gateway, all internal nodes had no internet → RKE2 installer failed
   - **Fix**: Changed to `false`. Comment added explaining Zarf-on-AWS needs internet for RKE2 bootstrap.
   - **Remediation**: `aws:provision` task should validate airgap_mode vs. deployment strategy, or the variable should be removed from tfvars and set only via CLI flag.

2. **No StorageClass on RKE2 — Zarf init PVC stuck Pending**
   - **Impact**: 41 minutes wasted waiting, then manual cleanup + retry
   - **Fix**: Manually installed `local-path-provisioner` from rancher/local-path-provisioner GitHub
   - **Remediation**: `cluster-only.yml` playbook should install local-path-provisioner as a role, or `aws:deploy:zarf` task should check for StorageClass and install if missing.

3. **Bastion /tmp is 957 MB tmpfs — can't stage 1.3 GB Zarf package**
   - **Impact**: Initial SCP to bastion failed silently
   - **Fix**: Used `ProxyCommand` SCP to send directly to CP, bypassing bastion disk
   - **Remediation**: The `aws:deploy:zarf` task already uses ProxyCommand. Consider increasing bastion tmpfs or using /home/ec2-user as staging.

4. **INGRESS_PROVIDER env var default doesn't work**
   - **Impact**: Cloudflare tunnel role skipped entirely (condition `ingress_provider == 'cloudflare'` was false)
   - **Fix**: Manually set `export INGRESS_PROVIDER=cloudflare` before Ansible
   - **Root cause**: `lookup('env', 'INGRESS_PROVIDER') | default('cloudflare')` returns empty string (not undefined) when env not set. Jinja2 `default()` only fires on undefined, not empty.
   - **Remediation**: Change to `lookup('env', 'INGRESS_PROVIDER') | default('cloudflare', true)` or use `or 'cloudflare'` idiom: `{{ lookup('env', 'INGRESS_PROVIDER') or 'cloudflare' }}`

5. **Zarf agent mutates cloudflared image reference**
   - **Impact**: cloudflared pods in ImagePullBackOff — image `127.0.0.1:31999/cloudflare/cloudflared:2026.2.0-zarf-*` doesn't exist in Zarf registry
   - **Fix**: Labeled namespace `zarf.dev/agent=ignore`, deleted mutated deployment, re-ran Ansible
   - **Remediation**: Ansible cloudflare-tunnel role should add `zarf.dev/agent=ignore` label to namespace automatically. Or include cloudflared in the Zarf package.

6. **Zarf init package not pre-staged on CP**
   - **Impact**: Had to download 390 MB `zarf-init-amd64-v0.74.0.tar.zst` separately
   - **Fix**: Downloaded from GitHub on the CP (via NAT gateway)
   - **Remediation**: `aws:deploy:zarf` task should SCP the init package along with the app package. Or pre-bake it into the AMI.

### Non-critical

7. **Zarf image push port-forward timeout (first deploy attempt)**
   - **Impact**: ~10 minutes wasted, retry worked because layers were cached
   - **Root cause**: 658 MB cybersec-dask image push through port-forward is flaky
   - **Remediation**: May need to increase Zarf's timeout or use a different push mechanism for large images.

8. **sample-notebooks YAML parsing error**
   - **Impact**: Sample notebooks not deployed (non-critical for E2E verification)
   - **Error**: `wrong node kind: expected MappingNode but got ScalarNode` in `manifests/sample-notebooks-configmap.yaml`
   - **Remediation**: Fix the ConfigMap YAML in the Zarf package. Likely a multi-line string quoting issue.

9. **`s3:PutBucketPublicAccessBlock` missing from IAM policy**
   - **Impact**: Warning in Ansible S3 role (ignored). Public access block not set on bucket.
   - **Remediation**: Add the permission to the IAM policy in `tofu/iam.tf`.

10. **Stale SSH CIDR in terraform.tfvars**
    - **Impact**: Would have blocked SSH from current IP
    - **Fix**: Updated from `216.147.122.181/32` to `216.147.124.22/32`
    - **Remediation**: Auto-detect developer IP in `aws:provision` or document the need to update.

11. **Zarf package version mismatch (built v0.66.0, deployed v0.74.0)**
    - **Impact**: None observed — deployment succeeded
    - **Remediation**: Align Zarf binary version with package build version. Pin in devenv.nix.

12. **CLOUDFLARE_TUNNEL_TOKEN / CLOUDFLARE_TUNNEL_ID must be manually exported**
    - **Impact**: Must extract from tofu output and export before running Ansible cloudflare role
    - **Remediation**: `aws:deploy:zarf` task should auto-extract these from `tofu output -json`.

## Automation Gap Analysis

### What worked well (fully automated)
- `aws:provision` — OpenTofu infrastructure + Cloudflare Zero Trust setup
- `aws:inventory` — Ansible inventory generation from tofu output
- `cluster-only.yml` — RKE2 cluster setup (common + server + agents + S3)
- Cloudflare tunnel routing config — correctly resolved Zarf service names

### What needs automation
1. **StorageClass provisioning** — Add to `cluster-only.yml`
2. **Zarf init + deploy** — The new `aws:deploy:zarf` task covers this, but needs:
   - Zarf init package SCP
   - StorageClass check
   - `INGRESS_PROVIDER` env var handling
   - Cloudflare tunnel namespace labeling
3. **Cloudflare tunnel env vars** — Auto-extract from tofu output
4. **End-to-end orchestration** — Single `aws:e2e` task that chains provision → inventory → cluster → zarf → tunnel → verify

### Legacy Ansible roles (now superseded by Zarf)
These roles are no longer needed for AWS deployment:
- `roles/dask` — Zarf deploys Dask operator + DaskCluster
- `roles/panel-viz` — Zarf deploys OTEL Navigator + PTY proxy
- `roles/jupyterhub` — Zarf deploys JupyterHub via Helm
- `roles/datagen` — Zarf deploys NavigatorEngine

Consider marking as deprecated or removing.

## Verification Checklist

- [x] 9/9 K8s nodes Ready (1 CP + 8 workers, RKE2 v1.34.5)
- [x] 16/16 Dask workers connected to scheduler (103.1 GB total memory)
- [x] JupyterHub hub + proxy Running
- [x] OTEL Navigator (2/2) Running — Bokeh server on port 5006
- [x] PTY proxy sidecar Running — WebSocket server on port 8765
- [x] NavigatorEngine Running — gRPC on port 50051
- [x] Cloudflared 2/2 replicas — 4 tunnel connections to SJC edge
- [x] `https://dask.dev.aws.zndx.org` → 302 (WARP auth redirect) ✓
- [x] `https://jupyter.dev.aws.zndx.org` → 302 (WARP auth redirect) ✓
- [x] `https://viz.dev.aws.zndx.org` → 302 (WARP auth redirect) ✓
- [ ] Browser verification via WARP client (requires human interaction)
- [ ] WASM terminal WebSocket through Cloudflare tunnel (requires browser)
- [ ] Sample notebooks visible in JupyterHub (blocked by YAML parsing error)
- [ ] Dataset generation (50K spans) confirmed in S3 (not tested)
