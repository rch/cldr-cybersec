# AWS Re-deployment Summary (2026-03-30)

## Objective
Re-deploy full Cybersec Dask stack on AWS to validate OTEL Navigator UI.

## Infrastructure
- **Region**: us-west-1
- **Bastion**: 18.144.36.141
- **Control Plane**: 10.100.1.220 (m6i.xlarge)
- **Workers**: 8x r6i.xlarge (7 joined, worker-6 pending)
- **S3 Bucket**: cybersec-dask-00631868-data
- **RKE2**: v1.34.6+rke2r1
- **Zarf**: v0.74.0

## Deployment Steps
1. `aws:provision` - 256s (had to re-create SSH key pair)
2. `aws:inventory` - generated Ansible inventory
3. `ansible-playbook cluster-only.yml` - RKE2 + local-path-provisioner
4. Downloaded Zarf v0.74.0 binary directly on CP (Nix binary incompatible)
5. Downloaded init package on CP (390 MB, ~7s via NAT)
6. SCP'd app package (1.3 GB) via ProxyCommand (~8 min)
7. `zarf init --confirm --set REGISTRY_PVC_SIZE=5Gi` - ~45s
8. `zarf package deploy` - 3 attempts needed:
   - Attempt 1: Failed (pre-applied local-path-provisioner conflicted with Helm)
   - Attempt 2: Failed (same issue, `--components` doesn't skip defaults)
   - Attempt 3: Cleaned pre-applied resources, ran via nohup on CP - succeeded
9. `ansible-playbook site.yml --tags cloudflare` - Cloudflare tunnel deployed

## Issues Encountered

### cluster-only.yml wrong relative path
- `{{ playbook_dir }}/../../../zarf/` should be `../../../../zarf/`
- Committed fix: f062ace2

### Nix zarf binary incompatible with Amazon Linux
- Nix-built binary has Nix-specific library paths
- Fix: download from GitHub releases directly on CP

### Zarf Helm conflict with pre-applied resources
- `kubectl apply -f local-path-provisioner.yaml` creates non-Helm-managed resources
- Zarf wraps ALL manifests in Helm charts, fails on existing resources
- Fix: don't pre-apply, let Zarf handle it

### sample-notebooks YAML parsing error
- `sample-notebooks-configmap.yaml` has YAML parsing issue when Helm processes it
- Non-critical: notebooks can be deployed manually

## Verified Services
| Service | Status | NodePort | URL |
|---------|--------|----------|-----|
| Dask Scheduler | Running (4 workers, 8 threads, 24 GiB) | 30086/30087 | dask.dev.aws.zndx.org |
| JupyterHub | Running (hub + proxy) | 30080 | jupyter.dev.aws.zndx.org |
| Panel-Viz | Running (2/2 - app + PTY sidecar) | 30506 | viz.dev.aws.zndx.org |
| Navigator Engine | Running | - | - |
| Cloudflare Tunnel | Running (2 replicas) | - | *.dev.aws.zndx.org |
