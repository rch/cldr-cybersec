# panel-viz

Ansible role that deploys a Panel/HoloViews/Datashader heatmap visualization
on a Dask cluster. The app renders OTEL span latency data from S3-backed
parquet files, using `rasterize(dynamic=True)` for viewport-driven
re-rasterization across the distributed cluster.

## Architecture

```
Browser (Bokeh WebSocket)
  └─ Panel server (pod in panel-viz namespace)
       ├─ HoloViews rasterize(dynamic=True)  ← viewport callbacks
       │    └─ Datashader aggregation         ← runs on Dask workers
       └─ Dask Client → simple-scheduler.dask.svc:8786
                           └─ 32 workers (6 GiB each, r6i.xlarge)
                                └─ S3 parquet reads
```

## Smart Windowing

The core data-loading challenge is that the `otel-1t` dataset contains
**110,246 parquet files** (891 GiB, 15 billion spans). Naively passing all
files to `dd.read_parquet()` produces a 16+ MiB Dask task graph (~330K tasks)
that overwhelms the single-threaded scheduler before any worker executes.

The `load_span_data` function in `templates/panel-deployment.yaml.j2` solves
this with smart windowing:

### 1. Explicit file listing (no Hive discovery)

S3 files are found via `s3fs.glob()` with layout-aware patterns rather than
Dask's directory-based Hive partition discovery. This is necessary because
datasets have incompatible partition hierarchies:

| Dataset | Layout | Files |
|---|---|---|
| `otel-1t` | `shard=podNN/date=YYYY-MM-DD/batch_*.parquet` | 110,246 |
| `otel-minimal` | `date=YYYY-MM-DD/hour=HH/minimal_*.parquet` | 132 |
| `otel-large` | `date=YYYY-MM-DD/hour=HH/batch_*.parquet` | 1,699 |

Dask's Hive discovery crashes with `KeyError: 'hour'` when it encounters
mixed schemas under the same base path. Explicit file paths avoid this
entirely.

### 2. Partition cap (MAX_PARTITIONS = 200)

After date-filtering, if the file count exceeds 200, the loader applies
even sampling:

```
parquet_files.sort(reverse=True)          # newest dates first
step = total_files // MAX_PARTITIONS
parquet_files = parquet_files[::step][:MAX_PARTITIONS]
```

This keeps the Dask graph at ~400 KB / ~600 tasks. For an 800x500 heatmap,
200 partitions (~400M rows) yields identical visual fidelity to 110K
partitions — datashader aggregates everything down to ~400K pixels regardless.

Even sampling (every Nth file) is preferred over truncation (first N files)
because it preserves uniform temporal coverage across all dates in the
requested range.

### 3. Cached file index

The S3 glob for 110K files takes ~10-15 seconds. Results are cached in
`_file_list_cache` keyed by dataset path. The cache is cleared when the
dataset poller (`_poll_dataset_changes`) detects a switch (e.g.,
`otel-minimal` to `otel-1t`).

## Iteration History

This approach was reached after two failed alternatives:

1. **`rasterize(dynamic=True)` only** — correct rendering pattern (kept), but
   the bottleneck was graph size not rendering logic. Workers stalled at
   500 MB waiting for the scheduler to finish processing 330K tasks.

2. **`aggregate_files=True`** — Dask's built-in file batching. Crashed with
   `KeyError: 'hour'` due to mixed Hive partition schemas across datasets.

3. **Smart windowing** — explicit paths + `MAX_PARTITIONS` cap. No Hive
   discovery, predictable graph size, works with any directory layout.

## Configuration

Key defaults in `defaults/main.yml`:

| Variable | Default | Purpose |
|---|---|---|
| `panel_dask_scheduler` | `simple-scheduler.dask.svc:8786` | Dask cluster endpoint |
| `panel_otel_data_path` | `s3://{{ bucket }}/otel-minimal/` | Initial dataset path |
| `panel_s3_bucket` | from `s3_bucket_name` / env / default | S3 bucket for OTEL data |
| `panel_memory_limit` | `8Gi` | Pod memory limit |
| `panel_readiness_initial_delay` | `180` | Seconds for pip install + startup |
| `panel_debounce_ms` | `250` | Viewport callback debounce |

`MAX_PARTITIONS` (200) is defined inline in the app code within the
deployment template. Adjust it if the cluster size or dataset characteristics
change significantly.

## Dataset Auto-Swap

The datagen role writes `_active_dataset.json` to the S3 bucket root when
generation completes. The panel-viz app polls this marker every 60 seconds.
On change, it clears the file list cache and reloads from the new dataset.

## Future Directions: Unknown Dataset Layouts

The current implementation uses hardcoded glob patterns that encode knowledge
of the three known OTEL layouts (`shard=*/date=*/batch_*.parquet`, etc.). This
works but doesn't generalize. Several strategies are worth exploring for
datasets where the layout isn't known in advance.

### Recursive glob with sampling-first discovery

Instead of trying layout-specific patterns, do a single recursive glob
(`**/*.parquet`) and then infer structure from the results. The key insight is
that you don't need to glob *all* files to understand the layout — a prefix
scan of a few hundred paths is enough:

```python
# Discover layout from a sample rather than enumerating everything
sample = fs.glob(f"{base}/**/*.parquet", maxdepth=5)[:500]
# Infer partition keys from path components
# e.g. "shard=pod01/date=2026-02-15/batch_0001.parquet"
#   -> partition_keys = ['shard', 'date']
```

This separates discovery (what does this dataset look like?) from loading
(give me N representative files). The sample is tiny relative to 110K files
and answers the structural question immediately.

### Statistical sampling without layout knowledge

The even-sampling strategy (`files[::step][:N]`) is independent of directory
structure — it only requires a sorted list of paths. This means it works on
*any* parquet dataset regardless of partitioning scheme, as long as paths have
some temporal ordering (which lexicographic sort provides when dates appear in
paths).

For datasets where paths don't encode time at all, alternatives include:

- **Random sampling**: `random.sample(files, N)` gives uniform coverage
  without assuming anything about path structure. Statistically sound for
  aggregation-heavy workloads like heatmaps where spatial uniformity matters
  more than temporal ordering.

- **Footer-based sampling**: Read parquet footers (metadata only, no row data)
  from a random subset, extract min/max timestamps from row group statistics,
  then select files that cover the requested time range. This is layout-agnostic
  but requires one metadata read per sampled file.

- **Two-phase sampling**: Read a small random sample (e.g., 20 files), compute
  the actual time range present, then do a second targeted sample biased toward
  the requested window. Converges quickly for datasets with temporal locality.

### Catalog-driven discovery

The cleanest long-term solution is to avoid glob entirely and use an Iceberg
or Hive metastore catalog to resolve file lists. The catalog already knows the
partition layout, file locations, and column statistics. A query like
`SELECT file_path FROM catalog WHERE date BETWEEN x AND y` returns exactly the
files needed, with no glob, no layout guessing, and partition pruning built in.

This is the direction the broader pipeline is heading (Polaris REST catalog for
Iceberg tables). When the OTEL span data is written as a proper Iceberg table
rather than raw parquet files, the smart windowing logic can be replaced with a
catalog scan that returns pre-pruned, statistics-aware file lists.

### Adaptive partition budget

`MAX_PARTITIONS = 200` is a static budget based on the current cluster (32
workers, 6 GiB each). A more adaptive approach would calculate the budget
from cluster state:

```python
info = client.scheduler_info()
n_workers = len(info['workers'])
# Budget: enough partitions to keep all workers busy across 2-3 rounds
# but not so many that graph overhead dominates
max_partitions = n_workers * 8  # 256 for 32 workers
```

This would auto-scale if the cluster grows (more workers = higher budget) or
shrinks (fewer workers = tighter budget), without changing the code.

### Graph size as the constraint, not file count

The real constraint is Dask graph serialization size, not file count. A 200-file
dataset with `split_row_groups=True` could produce 2000 partitions if each file
has 10 row groups. A better cap would target graph size directly:

```python
# Estimate: ~1.5 KB per partition in the graph (path string + read task + column select)
TARGET_GRAPH_KB = 500
estimated_partitions = len(files)  # 1 partition per file with split_row_groups=False
if estimated_partitions * 1.5 > TARGET_GRAPH_KB:
    step = int(estimated_partitions * 1.5 / TARGET_GRAPH_KB)
    files = files[::step][:TARGET_GRAPH_KB // 1.5]
```

This makes the approach robust to varying file sizes and row group counts.

## Files

```
panel-viz/
├── defaults/main.yml                    # Role variables
├── files/requirements.txt               # Python deps (panel, hvplot, datashader, etc.)
├── tasks/main.yml                       # Ansible tasks
├── templates/panel-deployment.yaml.j2   # K8s Deployment with inline Panel app
└── README.md                            # This file
```

The Panel app is embedded inline in the Deployment template rather than built
as a container image. This allows rapid iteration via `ansible-playbook`
without a container build/push cycle.
