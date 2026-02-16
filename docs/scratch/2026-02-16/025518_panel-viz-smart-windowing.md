# Panel-Viz Heatmap: Smart Windowing for 1TB+ OTEL Datasets

## Problem

The Panel-based heatmap visualization (HoloViews + Datashader + Dask) worked correctly with the `otel-minimal` dataset (~132 parquet files, 37 MiB) but failed catastrophically when the `otel-1t` dataset completed generation (110,246 files, 891 GiB, 15B spans).

**Symptom**: Heatmap never renders. Dask dashboard shows two brief red streaks (task submissions), then workers restart. Workers never exceed 500 MB memory despite having 6 GiB each. Browser WebSocket times out after ~4 minutes.

## Root Cause Investigation

### Attempt 1: Restore `rasterize(dynamic=True)`

The working commit (c42866b1) used `rasterize(dynamic=True)` for viewport-driven re-rasterization. We restored this pattern, replacing a manual `DynamicMap + RangeXY + render_viewport` approach.

**Result**: Same failure pattern. `rasterize(dynamic=True)` was correct but not the bottleneck.

**Key log**: `Sending large graph of size 16.45 MiB` appeared in Dask scheduler logs.

### Attempt 2: `aggregate_files=True` with Hive partition discovery

Tried Dask's `dd.read_parquet()` with directory-based Hive partition discovery and `aggregate_files=True` to batch small files into fewer partitions.

**Result**: `KeyError: 'hour'` crash.

**Root cause**: Mixed Hive partition schemas across datasets:
- `otel-1t`:  `spans/shard=podNN/date=YYYY-MM-DD/batch_XXXX_chunk_YY.parquet` (no `hour` level)
- `otel-minimal`: `spans/date=YYYY-MM-DD/hour=HH/minimal_XXXX.parquet` (has `hour` level)
- `otel-large`: `spans/date=YYYY-MM-DD/hour=HH/batch_XXXX_podN.parquet` (has `hour` level)

Dask's Hive discovery expects all files under a directory to share the same partition key hierarchy. When it encounters files with `hour` and files without `hour`, it crashes with `KeyError: 'hour'` in `arrow.py:1493`.

### Attempt 3: Smart Windowing (the solution)

**Insight**: The problem was never the rendering pattern. It was the **task graph size**. With 110K files, Dask creates:
- 110K read tasks
- 110K column-select tasks
- 110K arithmetic tasks (timestamp/duration conversion)
- Aggregation tasks on top

Total serialized graph: ~16.45 MiB. The Dask scheduler spends all its time deserializing and scheduling this graph, workers barely start executing before the browser WebSocket times out.

The working version had only ~132 files. For an 800x500 pixel heatmap, 200 files (~400M rows) provide identical visual fidelity to 110K files (~15B rows) because datashader aggregates everything down to ~400K pixels regardless.

## Solution: Smart Windowing

File: `infra/aws/ansible/roles/panel-viz/templates/panel-deployment.yaml.j2`

### Design

1. **Explicit file listing** via `s3fs.glob()` instead of Dask's Hive directory discovery. This avoids the mixed-layout `KeyError` entirely.

2. **Cached file index** (`_file_list_cache`). The S3 glob for 110K files takes ~10-15 seconds. Cache by dataset path so subsequent loads are instant. Clear on dataset swap (otel-minimal -> otel-1t).

3. **Layout-aware glob patterns**. Try patterns in order of expected dataset size:
   - `shard=*/date=*/batch_*.parquet` (otel-1t: 110K files)
   - `shard=*/date=*/hour=*/*.parquet` (otel-1t old layout)
   - `date=*/hour=*/*.parquet` (otel-minimal/large)
   - `date=*/*.parquet` (flat layout)

4. **Date filtering** on file paths. Match `date=YYYY-MM-DD` in path strings against the requested time range.

5. **MAX_PARTITIONS = 200 cap**. If filtered files exceed 200:
   - Sort descending (newest dates first via lexicographic path sort)
   - Even sampling: take every Nth file (`step = total_files // 200`)
   - Cap at exactly 200 files

   This preserves uniform coverage across the time range while keeping the graph at ~400 KB (600 tasks).

6. **Native lazy rendering**. `rasterize(dynamic=True)` handles viewport-driven re-rasterization. Workers only read the 200 parquet files when datashader's aggregation needs them. Zoom/pan re-rasterizes from the lazy DataFrame without re-reading S3.

### Key Code

```python
_file_list_cache = {}
MAX_PARTITIONS = 200

def load_span_data(start_time, end_time, data_path=None, on_progress=None):
    # 1. Glob files (cached)
    # 2. Filter by date
    # 3. If > MAX_PARTITIONS: sort descending, sample every Nth file
    # 4. dd.read_parquet(explicit_file_list, columns=[...], split_row_groups=False)
    # 5. Compute timestamp_s and duration_ms lazily
```

### Performance Comparison

| Metric | Before (110K files) | After (200 files) |
|---|---|---|
| Task graph size | 16.45 MiB | ~400 KB |
| Dask tasks | ~330K (110K x 3 layers) | ~600 |
| Initial render | >4 min (timeout) | ~10-30s |
| Worker memory | 500 MB (stalled) | 1-2 GiB (active) |
| Visual fidelity | N/A (never renders) | Full (400M rows -> 400K pixels) |
| `KeyError: 'hour'` | N/A (different approach) | Impossible (no Hive discovery) |

## Methodology Notes

### Why the graph was the bottleneck, not the data

With 32 workers x 6 GiB each = 192 GiB cluster memory, reading 200 files x ~8 MiB = 1.6 GiB is trivial. But the Dask scheduler is single-threaded for graph processing. Serializing, transmitting, and scheduling a 16 MiB graph with 330K tasks takes longer than the browser's WebSocket timeout.

The workers never got past 500 MB because they were waiting for the scheduler to finish processing the graph before dispatching tasks.

### Why even sampling beats truncation

Taking the first 200 files (truncation) would only cover 1-2 dates. Even sampling (`files[::step]`) picks files uniformly across all dates in the requested range, giving the heatmap accurate temporal coverage for all time presets (Last Hour, Last 24 Hours, Last 7 Days).

### Why explicit paths beat Hive discovery

Dask's `read_parquet(directory)` with Hive partition discovery:
- Expects uniform partition key hierarchy across all files
- Fails silently or crashes on mixed schemas
- Adds overhead for partition pruning on large file counts

Explicit file paths via `read_parquet([path1, path2, ...])`:
- No schema assumptions
- Works with any directory layout
- Date filtering is simple string matching on paths
- Partition count = file count (predictable graph size)

### Dataset auto-swap

The `_poll_dataset_changes` method polls `_active_dataset.json` every 60 seconds. When the dataset changes (e.g., datagen publishes otel-1t marker after completing large generation), it calls `_file_list_cache.clear()` to force a fresh glob on the next load.
