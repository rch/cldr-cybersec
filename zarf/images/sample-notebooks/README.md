# Sample Notebooks

These notebooks demonstrate out-of-core processing with Dask and S3.

## Available Notebooks

| Notebook | Description | Data Size |
|----------|-------------|-----------|
| `OTEL_Data_Generator.ipynb` | Generate synthetic OTEL spans (same methodology as 1TB dataset) | Configurable |
| `Dask_S3_Validation.ipynb` | Out-of-core Dask stress test with 30GB dataset | 30 GB |
| `HDF5_CPHY_Acquisition_Generator.ipynb` | CPHY/OTel HDF5 + Dask/datashader (idempotent Run All) | lab ~10 GiB / lab_tiny ~6 MiB / airgap ~2 TiB |

## Getting Started

These notebooks are **read-only** under `~/sample-notebooks/` (ConfigMap).
JupyterLab opens in `$HOME` (`/root`); copy a notebook to the top level to edit:

```bash
cp ~/sample-notebooks/HDF5_CPHY_Acquisition_Generator.ipynb ~/
```

The CPHY HDF5 notebook is **idempotent by default**: Run All reuses existing
full-size parts under `s3://…/datasets/hdf5/…`. Set `FORCE_REGENERATE = True`
(or `HDF5_FORCE_REGENERATE=1`) only when you want a fresh write.

## Environment Variables

The following are pre-configured (local lab: RustFS `admin`/`admin`, bucket `cyberphy`):
- `DASK_SCHEDULER_ADDRESS`: Dask cluster endpoint
- `S3_ENDPOINT`: S3 endpoint (RustFS on lab nodes; empty for AWS)
- `S3_BUCKET`: data bucket (default `cyberphy` in notebooks)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: S3 credentials
- `HDF5_PROFILE` / `HDF5_FORCE_REGENERATE`: optional CPHY generator overrides

## Cluster Resources

Default package: **4 workers** × 2 threads × 6 GiB (multi-core baseline).  
CPU limit is templated to match threads (avoid 1 CPU / 2 nthreads stall).

| Workload | Replicas | Notes |
|----------|----------|--------|
| Smoke | 1–2 | tiny hosts |
| Multi-core lab | 8–16 | this class of box |
| Air-gap 2 TiB HDF5 | 16–32 | wide part fan-out |

```bash
# Prefer DaskCluster CR (operator source of truth)
kubectl -n dask patch daskcluster cybersec-dask --type merge \
  -p '{"spec":{"worker":{"replicas":8}}}'

# At zarf deploy
# --set DASK_WORKER_REPLICAS=8 --set DASK_WORKER_NTHREADS=2 --set DASK_WORKER_CPU=2
```

## Holoviews in air-gap

Use embedded Bokeh resources (no CDN):

```python
import os
os.environ.setdefault("BOKEH_RESOURCES", "inline")
import holoviews as hv
hv.config.image_rtol = 1.0
hv.extension("bokeh", inline=True)
```

- **Dask_S3_Validation**: density plot uses `datashader.Canvas` + coords-only `hv.Image`
  so zoom re-aggregates on Dask workers without the Holoviews 1.23 Image bounds error.
- **HDF5_CPHY**: generation is idempotent (`ensure_parts`); viz uses INLINE/PNG fallback.

### HDF5 + Dask

`HDF5_CPHY_Acquisition_Generator.ipynb` builds a multi-file `dask.array` (one task
per hive part). Reductions run on workers. The heatmap is **viewport-driven**: each
pan/zoom slices `values_da` and `.compute()`s only overlapping parts (same contract
for lab ~10 GiB and multi-TB). Tune with `SERIES_STRIDE`, `TIME_STRIDE`, `MAX_PARTS`,
and `TARGET_WORKERS`.

