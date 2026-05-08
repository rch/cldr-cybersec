# OTEL Navigator: Data Generation & Polling Fix (2026-03-30)

## Data Generation

Generated 50K minimal OTEL spans on the Dask scheduler pod using IAM role credentials (no static AWS keys needed).

- **Script**: Standalone `gen-otel-minimal.py` using `pyarrow.fs.S3FileSystem(region='us-west-1')`
- **Output**: `s3://cybersec-dask-00631868-data/otel-minimal/spans/` — 25 partitions (date/hour), 4.9 MiB
- **Marker**: `s3://cybersec-dask-00631868-data/_active_dataset.json` → `otel-minimal`
- **Note**: The original `generate-otel-data.py` script requires `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars. On AWS with IAM roles, these aren't set. Used a modified version that initializes `S3FileSystem` without explicit credentials, falling back to the botocore credential chain (instance profile).

## Bug Fix: Panel/Bokeh Thread Scoping

### Problem
`_poll_dataset_changes()` thread logged `name 'get_active_dataset' is not defined` every 60 seconds.

### Root Cause
Panel/Bokeh `serve` re-executes the script per-session in an isolated `exec()` globals dict. When a daemon thread started by one session outlives that session's globals (or runs in a different scope), module-level function names become inaccessible.

### Fix (`zarf/images/otel-navigator.py`)
Bind module-level functions to `self` in `__init__`, then reference via `self._*` in all daemon threads:

```python
# In __init__:
self._get_active_dataset = get_active_dataset
self._load_span_data = load_span_data
self._get_dask_client = get_dask_client
self._get_dask_stats = get_dask_stats
```

Updated callers: `_poll_dataset_changes()`, `_poll_dask_status()`, `_bg_load()`.

### Deployment
Used ConfigMap mount to persist the fix across container restarts:
```bash
kubectl create configmap -n panel-viz otel-navigator-script \
  --from-file=otel-navigator.py=/tmp/otel-navigator.py
kubectl patch deployment -n panel-viz otel-navigator -p '...'  # strategic merge patch
```

## Result
- Navigator loads 25 partitions across 4 Dask workers
- Phase: `Ready (otel-minimal: 25 partitions)`
- No more polling errors in logs
- Heatmap renders with Datashader pipeline
