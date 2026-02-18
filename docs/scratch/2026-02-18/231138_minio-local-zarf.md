# Local Zarf: Use MinIO Instead of AWS S3

## Summary

Wired the local Zarf deployment (`zarf:local:*` tasks) to use the host's MinIO instance
instead of AWS S3 credentials. "Local" means air-gap — no AWS dependency.

MinIO runs at `0.0.0.0:9010` with creds `minioadmin/minioadmin`, bucket `cybersec`,
region `us-east-1`. Pods reach it via the node's InternalIP (not localhost).

## Changes Made

| File | Change |
|------|--------|
| `devenv.nix` `zarf:local:deploy` | Node IP detection, MinIO health check, S3 `--set` flags passed to `zarf package deploy` |
| `devenv.nix` `zarf:local:preflight` | Quick MinIO warning before conftest run |
| `devenv.nix` `zarf:local:status` | MinIO section (healthy/endpoint/creds) + pod-reachability in Service Accessibility |
| `policy/k8s/local/requirements.rego` | `services` alias, MinIO deny rule, MinIO info rule, updated "Ready" check |
| `.env` | Removed `OTEL_S3_BUCKET=cybersec-otel-data` (not needed for local deploy) |
| `cybersec/zarf/local.py` | Added `minio_endpoint`, `minio_bucket` to config output |

## What Did NOT Change

- `zarf/zarf.yaml` — variable definitions already have empty defaults, populated via `--set`
- `zarf/manifests/panel-viz.yaml` — already templates `S3_ENDPOINT`, `S3_BUCKET`, etc.
- `zarf/manifests/dask-cluster.yaml` — already templates S3 vars into scheduler+worker pods
- `zarf/manifests/jupyterhub-values.yaml` — already templates S3 vars into singleuser pods

## Operational Note

The AWS bucket `s3://cybersec-otel-data` in `us-west-1` should be manually deleted:
```bash
aws s3 rb s3://cybersec-otel-data --force --region us-west-1
```
