# Zarf air-gap releases

**Primary product delivery** for the interactive stack on closed-world Kubernetes (RKE2).

## What ships

| Component | Role |
|-----------|------|
| Dask operator + cluster | Distributed compute for OTel / parquet exploration |
| JupyterHub | Notebooks (generators, validation) on the same image |
| Panel-Viz (OTEL Navigator, Data-View) | Interactive heatmaps / windows over Iceberg/S3 data |
| Navigator engine + PTY | Terminal-driven control plane for the UI |
| Sample notebooks | OTEL data generator, S3/Dask validation |
| Bootstrap images | local-path-provisioner where needed |

Package definition: `zarf/zarf.yaml`. Image: historically `cybersec-dask` (rebrand to cyberphy naming is incremental).

## Operator entrypoints

| Script / module | Purpose |
|-----------------|--------|
| `zarf/scripts/converge-node.sh` | Node-local verify / apply / teardown |
| `zarf/converge/` | Catalog of invariants + remediations |
| `zarf/scripts/verify-s3-datapath.sh` | ConfigMap bucket + marker + span parquet |
| `zarf/scripts/ops.sh` | Image tag / package / redeploy helpers |

## Docs in the package tree

| File | Content |
|------|---------|
| `zarf/README.md` | Package contents, ports, deploy variables |
| `zarf/RUNBOOK.md` | Step-by-step deploy |
| `zarf/AIRGAP-CONVERGE-RUNBOOK.md` | Converge-driven lifecycle |
| `zarf/AIRGAP-DISCOVERY.md` | Read-only state checks |
| `zarf/AIRGAP-REMEDIATION-COMMANDS.md` | Manual kubectl/zarf mirror of engine rem |

## Config-only vs full package

- **Config-only** (blank `S3_BUCKET` / `s3:///`, deploys already exist): pass S3_* via creds file → `converge-node.sh apply` patches CM/Secret + rollout — **no** 1.3 GiB package required.  
- **Component / image changes**: `zarf package deploy --components=…` (or full package).  
- **Never required for S3 config alone:** cluster teardown or Cloudera Manager.

## Typical URLs (NodePort)

| Service | Port (default package) |
|---------|------------------------|
| OTEL Navigator | 30506 |
| PTY / terminal WS | 30765 |
| JupyterHub | 30080 |
| Dask dashboard | 30087 |

See also [Converge & verification](./converge.md) and [Air-gap deployment](../operations/airgap-deployment.md).
