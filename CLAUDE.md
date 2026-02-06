# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cybersec Toolkit is a data pipeline for ingesting, correlating, and preparing cybersecurity data for analytics. It builds a Security Data Lakehouse using Apache Flink, Apache Iceberg, and PostgreSQL with a focus on CloudTrail event processing.

## Build Commands

### Java (flink-cyber toolkit)
```bash
cd flink-cyber
mvn clean install              # Build with tests
mvn clean install -DskipTests  # Build without tests
```

### Python
```bash
uv sync                        # Install dependencies
uv run pytest                  # Run tests
uv run python <script.py>      # Run Python scripts
```

## Development Environment

The project uses [devenv](https://devenv.sh/) for local development:

```bash
devenv up                              # Start core services (Flink, Iceberg, Prometheus)
devenv tasks run polaris:check         # Verify Polaris configuration
devenv tasks run restart:clean         # Clean restart all services
devenv tasks run docs:build            # Build mdbook documentation
```

### K8s Stack (on-demand)

K8s services are provisioned via tasks, not started automatically:

```bash
devenv tasks run k8s:provision         # Provision k3d cluster
devenv tasks run k8s:deploy-dask       # Deploy Dask operator + cluster
devenv tasks run k8s:deploy-jupyter    # Deploy JupyterHub
devenv tasks run k8s:forward           # Start port-forwards (Dask:8787, JupyterHub:8000)
devenv tasks run k8s:status            # Check K8s status
devenv tasks run k8s:destroy           # Delete k3d cluster
```

For existing RKE2 clusters:
```bash
export KUBECONFIG=~/.kube/rke2.yaml
devenv tasks run k8s:deploy-dask       # Deploy to RKE2
```

### Service Ports (Core Stack - always started)
- Flink Web UI: http://localhost:8081
- Iceberg Browser: http://localhost:5050
- MinIO Console: http://localhost:9011 (minioadmin/minioadmin)
- Apache Polaris REST: http://localhost:8181
- PostgreSQL: port 5438
- OpenTelemetry Collector: ports 4317 (gRPC), 4318 (HTTP), 8889 (Prometheus)
- Prometheus: http://localhost:9090
- NiFi Web UI: http://localhost:8450
- NiFi OTLP Receiver: port 4319 (receives traces from OTEL Collector)

### K8s Stack Ports (after k8s:forward)
- Dask Dashboard: http://localhost:8787
- Dask Scheduler: port 8786
- JupyterHub: http://localhost:8000
- Kubernetes Dashboard: https://localhost:10443
- K3d API Server: port 6550 (only when target=k3d)

## Architecture

### Data Flow
```
Flink DataGen → Kafka (cloudtrail-raw) → Flink Processor → Kafka (cloudtrail-parsed) → PyIceberg Writer → PostgreSQL Catalog + MinIO Storage
```

### Key Components

**Python Pipeline** (root directory):
- `flink_jobs/cloudtrail_datagen.py` - Generates synthetic CloudTrail events
- `flink_jobs/cloudtrail_processor.py` - Parses and enriches events
- `iceberg_writer/cloudtrail_writer.py` - Persists to Iceberg format
- `iceberg_writer/cloudtrail_query.py` - Python query interface
- `iceberg_browser.py` - Flask web UI for browsing Iceberg data
- `main.py` - Pipeline orchestrator

**Java Toolkit** (`flink-cyber/`):
- `parser-chains-flink/` - Log parsing pipeline
- `flink-enrichment/` - Event enrichment (CIDR, geocode, ThreatQ, HBase lookup)
- `flink-indexing/` - Iceberg/Hive indexing
- `flink-profiler-java/` - Event profiling
- `flink-alert-scoring/` - Alert scoring system
- `cyber-jobs/` - Pre-built Flink job configurations
- `cyber-parcel/` & `cyber-csd/` - Cloudera Manager packaging

### Infrastructure (`devenv.nix`)
- PostgreSQL 16 with pg_cron and Apache AGE extensions
- MinIO for S3-compatible object storage
- Apache Polaris REST catalog for Iceberg
- Custom Flink 1.20.1 build (in `thirdparty/flink/`) for Iceberg compatibility
- Apache NiFi 2.0.0 binary (in `thirdparty/nifi/`) for data flow visualization
- Automatic Polaris bootstrap and catalog initialization

## Key Configuration

### Versions (from `flink-cyber/pom.xml`)
- Flink: 1.20.1 (Apache, built from source)
- Iceberg: 1.9.0
- Java: 1.8 for compilation
- Scala: 2.12

### Environment Variables
- `ICEBERG_CATALOG_URI`: PostgreSQL connection (default: `postgresql://postgres@localhost:5438/cybersec`)
- `ICEBERG_WAREHOUSE`: S3 path (default: `s3://cybersec/iceberg/warehouse`)
- `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`: MinIO credentials (minioadmin/minioadmin)
- `S3_ENDPOINT`: MinIO endpoint (http://localhost:9010)

## Testing

```bash
# Python tests
uv run pytest

# Java tests
cd flink-cyber && mvn test

# E2E pipeline tests
./test_complete_e2e.sh
uv run python test_complete_pipeline.py
```

## Polaris Catalog

Polaris bootstrap runs automatically on `devenv up`. Manual commands:
```bash
devenv tasks run polaris:init           # Re-initialize catalog
devenv tasks run polaris:check          # Verify configuration
devenv tasks run polaris:bootstrap-verify # Full verification
```

Bootstrap credentials: admin/admin for POLARIS realm.

## Bootstrap System

The bootstrap system provides unified configuration and setup across CLI, Web UI, and MCP interfaces.

### First-Time Setup

On `devenv up`, the bootstrap-check process runs automatically and displays environment status. If bootstrap is needed:

1. **Web UI**: Visit http://localhost:5050/settings and click "Run Bootstrap"
2. **CLI**: Run `cybersec bootstrap run` or `uv run python -m cybersec.cli.main bootstrap run`
3. **MCP**: Use the `bootstrap_run` tool from Claude Code or other MCP clients

### Bootstrap CLI Commands

```bash
# Install the package first
uv pip install -e .

# Check current configuration
cybersec bootstrap info

# Check service health
cybersec bootstrap status

# View/modify settings
cybersec bootstrap settings --show
cybersec bootstrap settings --edit
cybersec bootstrap settings --set flink_home=/path/to/flink

# Run verification checks
cybersec bootstrap verify

# Run bootstrap process
cybersec bootstrap run
cybersec bootstrap run --flink-path ~/local/flink-1.20.1
cybersec bootstrap run --skip-flink
cybersec bootstrap run --dry-run

# Quick assessment (for automation)
cybersec bootstrap assess
```

### Bootstrap Configuration

Configuration is stored in `.cybersec/config.toml`:

```toml
[bootstrap]
completed = true
last_run = "2025-01-23T12:00:00"

[paths]
flink_home = ""  # Empty = build from thirdparty/flink
minio_data_dir = ""  # Empty = $DEVENV_STATE/minio

[services.postgres]
host = "localhost"
port = 5438

[services.polaris]
api_url = "http://localhost:8181"
admin_url = "http://localhost:8182"

[catalog]
name = "cybersec"
warehouse = "s3://cybersec/iceberg/warehouse"
```

### MCP Server for AI Agents

Start the MCP server for Claude Code integration:

```bash
cybersec-mcp
# Or: uv run python -m cybersec.mcp.server
```

Available MCP tools:
- `bootstrap_info`: Get configuration and status
- `bootstrap_status`: Check service health
- `bootstrap_settings`: View/update settings
- `bootstrap_verify`: Run verification checks
- `bootstrap_run`: Execute bootstrap process
- `bootstrap_assess`: Quick assessment

### Module Structure

```
cybersec/
├── __init__.py
├── bootstrap/           # Core bootstrap library
│   ├── __init__.py
│   ├── config.py       # BootstrapConfig, SettingsManager
│   ├── state.py        # BootstrapState, TaskResult, TaskStatus
│   ├── events.py       # EventEmitter, EventType, BootstrapEvent
│   └── service.py      # BootstrapService (main orchestrator)
├── cli/                 # Typer CLI interface
│   ├── __init__.py
│   ├── main.py         # Main CLI app
│   └── bootstrap.py    # Bootstrap subcommands
└── mcp/                 # MCP server (fastmcp)
    ├── __init__.py
    └── server.py       # MCP tools and resources
```

### Web UI Routes

Bootstrap routes are integrated into iceberg_browser.py:
- `/settings` - Bootstrap settings page
- `/api/bootstrap/info` - Configuration API
- `/api/bootstrap/status` - Service health API
- `/api/bootstrap/settings` - Settings GET/POST API
- `/api/bootstrap/verify` - Verification API
- `/api/bootstrap/run` - Bootstrap execution (SSE stream)
- `/api/bootstrap/assess` - Quick assessment API

## Health Diagnostics

The health system provides FMEA-based diagnostics and automated remediation.

### Commands (CLI and MCP use identical syntax)

```bash
# CLI usage: cybersec "<command>"
# MCP usage: cmd("<command>")

# Run health checks
/health                    # All categories
/health flink              # Flink category only
/health --quick            # Critical checks only

# Fix detected issues (fix-all mode is default)
/health fix                # Dry-run all issues
/health fix --apply        # Apply all fixes

# Fix by category
/health fix flink          # Dry-run flink issues
/health fix flink --apply  # Fix flink issues

# Fix specific failure mode
/health fix INFRA_004 --apply

# Diagnose specific failure mode
/health diagnose FLINK_001

# Bootstrap commands
/bootstrap status          # Check service health
/bootstrap run             # Run bootstrap process
/bootstrap info            # Show configuration
```

### Categories

Provider-agnostic naming for swappable components:
- `flink` - Flink and PyFlink issues
- `nifi`, `kafka` - Future Cloudera OSS components
- `rest-catalog` - REST catalog (Polaris)
- `local-s3` - Local S3 storage (MinIO)
- `aws-s3` - AWS S3 (future)
- `postgres` - PostgreSQL database
- `system` - OS-level issues (shared memory, eBPF)
- `infra` - Infrastructure (terraform/ansible)
- `data` - Data quality checks

Aliases: `iceberg` → `rest-catalog`, `pyflink` → `flink`

## Operations Agent (@ops)

The Operations Agent (`@ops`) provides automated environment validation, service health monitoring, and operational verification. **Invoke this agent when:**

- Performing a clean restart (`devenv tasks run restart:clean`)
- Running code quality checks (type checking, linting, tests)
- Verifying normal operations after code changes
- Troubleshooting service failures
- Setting up the environment for a new developer
- Validating E2E functionality before commits

### Usage

```
@ops check the environment health
@ops perform a clean restart and verify E2E
@ops troubleshoot why Flink isn't starting
@ops verify all services before I commit
```

### Flink Provisioning (Required for E2E)

Flink must be built or configured for complete E2E functionality:

```bash
# Option A: Build from source (recommended)
cd thirdparty/flink
git submodule update --init --recursive
mvn clean install -DskipTests -Dfast

# Option B: Use existing installation
cybersec bootstrap settings --set flink_home=/path/to/flink-1.20.1
```

### E2E Validation Checklist

Complete E2E requires all services healthy:
- PostgreSQL (5438)
- Polaris REST API (8181) + Admin (8182)
- MinIO (9010)
- Iceberg Browser (5050)
- Flink JobManager (8081) + TaskManager
- Iceberg catalog 'cybersec' in Polaris
- OpenTelemetry Collector (4317/4318/8889)
- Prometheus (9090)
- NiFi (8450) + OTLP receiver (4319)

### NiFi Provisioning

NiFi provides data flow visualization and receives OTEL traces:

```bash
# Download NiFi binary
./scripts/setup_nifi_bin.sh 2.0.0

# Or use existing installation
cybersec bootstrap settings --set nifi_home=/path/to/nifi-2.0.0

# Verify NiFi
curl http://localhost:8450/nifi-api/system-diagnostics | jq '.systemDiagnostics.aggregateSnapshot.usedHeap'
```

### Observability Stack

The environment includes a full observability stack:

**OpenTelemetry Collector** receives telemetry via OTLP (gRPC:4317, HTTP:4318):
- Traces: forwarded to NiFi (port 4319) for flow visualization
- Metrics: exported to Prometheus (port 8889)

**Prometheus** (port 9090) scrapes metrics from the OTEL Collector at 1-second intervals with 15-day retention.

```bash
# Verify OTEL metrics
curl http://localhost:8889/metrics | head

# Query Prometheus
curl 'http://localhost:9090/api/v1/query?query=up'
```

### Future Enhancements

The ops agent will be extended with:
- **Conftest**: Infrastructure policy validation, security baselines
- **Grafana**: Metrics visualization dashboards
- **Alerting**: Threshold-based notifications
