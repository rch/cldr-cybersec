# Benchmark Infrastructure

Kubernetes manifests for running Dask scaling benchmarks.

## Files

| File | Description |
|------|-------------|
| `benchmark-job.yaml` | Kubernetes Job that runs Datashader benchmark against Dask cluster |

## Usage

### Run Benchmark Job on Kubernetes

```bash
# Apply the job
kubectl apply -f infra/benchmarks/benchmark-job.yaml

# Watch logs
kubectl logs -f job/dask-benchmark -n dask

# Get results (saved to /tmp/benchmark_results.json in pod)
kubectl cp dask/$(kubectl get pods -n dask -l job-name=dask-benchmark -o jsonpath='{.items[0].metadata.name}'):/tmp/benchmark_results.json ./results.json
```

### Run Benchmark Script Locally

For more control, use the Python benchmark runner:

```bash
# Connect to existing Dask scheduler
python scripts/run_benchmark.py --scheduler tcp://dask-scheduler:8786

# Quick profile (smaller dataset)
python scripts/run_benchmark.py --scheduler tcp://... --profile quick

# Full benchmark
python scripts/run_benchmark.py --scheduler tcp://... --profile full

# Custom worker counts
python scripts/run_benchmark.py --scheduler tcp://... --workers 2,4,8,16
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DASK_SCHEDULER` | Scheduler address | — |
| `AWS_PROFILE` | AWS profile for S3 | `default` |
| `S3_BUCKET` | Override S3 bucket | `cybersec-dask-data` |

## Benchmark Configuration

The `benchmark-job.yaml` runs a Datashader benchmark that:

1. Connects to Dask scheduler at `tcp://simple-scheduler:8786`
2. Loads OTel span data from S3 (`s3://cybersec-dask-data/otel/validation-30gb`)
3. Runs aggregation benchmarks at multiple zoom levels
4. Outputs timing results to JSON

### Dataset

The benchmark expects a 30GB synthetic OTel dataset. Generate it using the Dask S3 Validation notebook or the benchmark script's auto-generation feature.

## Related Files

- `scripts/run_benchmark.py` - Python benchmark runner
- `cybersec/benchmarks/` - Benchmark modules (config, metrics, analysis, figures)
- `infra/dask/dask-cluster.yaml` - Local Dask cluster manifest
- `infra/aws/ansible/roles/dask/` - AWS Dask deployment
