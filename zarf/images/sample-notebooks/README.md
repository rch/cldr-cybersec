# Sample Notebooks

These notebooks demonstrate out-of-core processing with Dask and S3.

## Available Notebooks

| Notebook | Description | Data Size |
|----------|-------------|-----------|
| `OTEL_Data_Generator.ipynb` | Generate synthetic OTEL spans (same methodology as 1TB dataset) | Configurable |
| `Dask_S3_Validation.ipynb` | Out-of-core Dask stress test with 30GB dataset | 30 GB |

## Getting Started

These notebooks are **read-only** (baked into the image). To edit and run:

```bash
cp /app/sample-notebooks/OTEL_Data_Generator.ipynb ~/
```

## Environment Variables

The following are pre-configured (local lab: RustFS `admin`/`admin`, bucket `cyberphy`):
- `DASK_SCHEDULER_ADDRESS`: Dask cluster endpoint
- `S3_ENDPOINT`: S3 endpoint (RustFS on lab nodes; empty for AWS)
- `S3_BUCKET`: data bucket (default `cyberphy` in notebooks)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: S3 credentials

## Cluster Resources

Default Dask cluster: 32 workers x 6 GiB = 192 GiB

To scale workers:
```bash
kubectl scale deployment cybersec-dask-default-worker -n dask --replicas=64
```
