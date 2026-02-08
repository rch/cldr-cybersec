# Quick Start

Get the Cybersec Toolkit running on your laptop in under 5 minutes.

## Prerequisites

- [devenv](https://devenv.sh/) installed
- Git with submodule support
- 8GB+ available RAM

## 1. Clone and Initialize

```bash
git clone <repository-url>
cd cybersec
git submodule update --init --recursive
```

## 2. Start Core Services

```bash
devenv up
```

This starts:
- PostgreSQL (port 5438)
- MinIO (ports 9010/9011)
- Apache Polaris REST catalog (ports 8181/8182)
- Iceberg Browser (port 5050)
- OpenTelemetry Collector (ports 4317/4318/8889)
- Prometheus (port 9090)

## 3. Verify Environment

```bash
# Check service health
cybersec bootstrap status

# Or via the CLI
uv run python -m cybersec.cli.main bootstrap status
```

## 4. Build Flink (First Time Only)

For full E2E functionality, build Flink from source:

```bash
cd thirdparty/flink
mvn clean install -DskipTests -Dfast
cd ../..

# Restart to pick up Flink
devenv tasks run restart:clean
```

Or use an existing Flink installation:

```bash
cybersec bootstrap settings --set flink_home=/path/to/flink-1.20.1
```

## 5. Access the UI

| Service | URL | Credentials |
|---------|-----|-------------|
| Iceberg Browser | [http://localhost:5050](http://localhost:5050) | - |
| Flink Web UI | [http://localhost:8081](http://localhost:8081) | - |
| MinIO Console | [http://localhost:9011](http://localhost:9011) | minioadmin / minioadmin |
| Polaris REST | [http://localhost:8181](http://localhost:8181) | - |
| Prometheus | [http://localhost:9090](http://localhost:9090) | - |

PostgreSQL: `psql -h localhost -p 5438 -U postgres -d cybersec`

## Pipeline Overview

```
┌──────────────┐
│ Flink DataGen│ Generates synthetic CloudTrail events (100/sec)
└──────┬───────┘
       │ Direct write to Iceberg
       ▼
┌──────────────┐
│ Iceberg Table│ Polaris REST Catalog + MinIO S3
└──────────────┘
```

The Java DataGen starts automatically with `devenv up` and writes 100 rows/sec to the `cybersec.default.cloudtrail_events` table.

## Common Commands

### Check Services

```bash
python main.py check
```

### Initialize Iceberg Catalog

```bash
python main.py init
```

### View Status

```bash
python main.py status
```

## Query Data

```python
from iceberg_writer.cloudtrail_query import CloudTrailQuery

query = CloudTrailQuery(
    "postgresql://postgres@localhost:5438/cybersec",
    "s3://cybersec/iceberg/warehouse"
)

# Recent events
events = query.query_recent_events(hours=24)

# Statistics
stats = query.get_event_statistics(hours=24)
```

## Key Features

- Synthetic CloudTrail event generation
- Direct Iceberg writes (no intermediate queues)
- Time + region partitioning for efficient queries
- PostgreSQL catalog for ACID transactions
- S3-compatible storage (MinIO)
- Python query interface
- Compatible with Spark, DuckDB, Trino

## Next Steps

Choose your workload:

- **[Laptop Development](./workloads/laptop-dev.md)**: Develop and test pipelines locally
- **[Workstation with GPUs](./workloads/workstation.md)**: Run security analysis with ML acceleration
- **[Benchmarking](./workloads/benchmarking.md)**: Performance testing across environments

## Troubleshooting

If services fail to start:

```bash
# Run health diagnostics
cybersec health

# Fix detected issues
cybersec health fix --apply

# Clean restart
devenv tasks run restart:clean
```

See [Troubleshooting](./reference/troubleshooting.md) for detailed guidance.
