# SC26 Benchmark Framework and AWS Cost Observability Implementation

## Summary

Implemented the SC26 Dask/Datashader Scaling Benchmark Framework and AWS Cost Observability Module as outlined in the plan.

## Benchmark Framework (Already Complete)

The benchmark framework was already fully implemented in `cybersec/benchmarks/`:

### Files
- `config.py` - BenchmarkConfig with 120GB dataset configuration
- `metrics.py` - BenchmarkMetrics, BenchmarkRun, ScalingResult dataclasses
- `dataset.py` - Seeded OTEL span generation with per-partition reproducibility
- `datashader_runner.py` - True out-of-core rendering (no .compute() before aggregation)
- `scaling.py` - ClusterManager for Dask cluster scaling
- `executor.py` - BenchmarkExecutor orchestration
- `analysis.py` - Statistical analysis (Amdahl's fraction, strong/weak scaling)
- `figures.py` - Publication-quality matplotlib figures (SC paper styling)
- `report.py` - Markdown, JSON, HTML report generation

### Commands (via MCP/CLI)
```bash
/benchmark generate          # Generate 120GB dataset to S3
/benchmark run               # Run scaling benchmarks (requires AWS Dask scheduler)
/benchmark analyze <path>    # Analyze results, generate figures
/benchmark suite             # Full reproduction suite
```

### Tests
- 46 tests passing in `cybersec/benchmarks/tests/`

## AWS Cost Observability (New)

Created `cybersec/cost/` module for continuous AWS cost monitoring:

### Files Created
- `__init__.py` - Module exports
- `config.py` - CostConfig, CostSource, ResourceCost, CostEstimate, CostDelta dataclasses
- `metrics.py` - Prometheus metric definitions (gauges, counters)
- `aws_client.py` - AWSCostClient with graceful AccessDenied handling
- `estimator.py` - ResourceEstimator with local pricing cache
- `monitor.py` - Flask + Prometheus metrics + background polling
- `tests/test_config.py` - Configuration tests
- `tests/test_estimator.py` - Estimator tests

### Commands (via MCP/CLI)
```bash
/cost status                 # Current MTD costs and budget status
/cost estimate               # Resource inventory estimation
/cost delta                  # Compare actual vs estimated (loss function)
/cost forecast               # AWS forecast and projections
```

### Architecture
```
┌─────────────────────────────────────────────────────────────────────┐
│                    cost-monitor process (devenv)                     │
│  Polls AWS every 5 minutes while devenv is active                    │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Prometheus Metrics Endpoint (:9876/metrics)             │
│                                                                      │
│  aws_cost_mtd_usd{account,region}                                   │
│  aws_cost_estimate_usd{account,source}                              │
│  aws_cost_delta_usd{account,period}                                 │
│  aws_cost_by_service_usd{account,service}                           │
│  aws_cost_forecast_usd{account,period}                              │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
┌───────────────────────────┐   ┌───────────────────────────────────┐
│    Prometheus (9090)      │   │   Query via CLI commands          │
│    scrapes every 60s      │   │   /cost status, /cost delta       │
│    15-day retention       │   │                                   │
└───────────────────────────┘   └───────────────────────────────────┘
```

### Configuration Changes

**devenv.nix:**
- Added `cost-monitor` process with readiness probe
- Added Prometheus scrape config for `cost-monitor` on port 9876

**pyproject.toml:**
- Added `prometheus-client>=0.19.0` to dependencies

**cybersec/commands/setup.py:**
- Added `register_cost_commands()` to command registration

### Tests
- 19 tests passing in `cybersec/cost/tests/`

## Verification

```bash
# Verify imports
uv run python -c "from cybersec.cost import CostConfig, CostMonitor; print('OK')"
uv run python -c "from cybersec.commands.setup import init_commands; init_commands(); print('OK')"

# Run tests
uv run pytest cybersec/benchmarks/tests/ -v  # 46 passed
uv run pytest cybersec/cost/tests/ -v        # 19 passed

# Check MCP help
cybersec "/help benchmark"
cybersec "/help cost"
```

## Usage

### Start cost-monitor (automatic with devenv up)
The cost-monitor process starts automatically when running `devenv up`.

### Manual cost queries
```bash
# Get current MTD costs
cybersec "/cost status"

# Get resource-based estimate
cybersec "/cost estimate --verbose"

# Compare estimate vs actual (loss function)
cybersec "/cost delta"

# Get AWS forecast
cybersec "/cost forecast"
```

### Environment Variables
```bash
AWS_PROFILE=default           # AWS credentials profile
AWS_REGION=us-east-1          # Primary region for Cost Explorer
COST_POLL_INTERVAL=300        # Poll every 5 minutes
COST_METRICS_PORT=9876        # Prometheus metrics port
COST_MONTHLY_BUDGET=500.0     # Monthly budget for alerts
```

## Notes

1. **Benchmark framework requires external Dask scheduler** - Local clusters are not supported. Use AWS infrastructure with `--scheduler tcp://host:port`.

2. **Cost Explorer access may be denied** - The module gracefully falls back to resource inventory estimation when Cost Explorer access is denied.

3. **MinIO vs AWS credentials** - The devenv separates `MINIO_*` credentials for local S3 from AWS credentials. Cost monitoring uses `AWS_PROFILE` from `~/.aws/credentials`.
