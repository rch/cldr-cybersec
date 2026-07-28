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
devenv up                              # Start ALL services (core + K8s stack)
devenv tasks run polaris:check         # Verify Polaris configuration
devenv tasks run restart:clean         # Clean restart all services
devenv tasks run docs:build            # Build mdbook documentation
```

### K8s Stack (autostarts with `devenv up`)

`devenv up` brings up the full stack, including the K8s group (Dask operator +
cluster, JupyterHub, port-forwards). The target is auto-detected by
`scripts/k8s_target_helper.sh` (single source of truth, also used by the
processes themselves):

1. `CYBERSEC_K8S_TARGET` env override (`k3d` | `rke2` | `none`)
2. RKE2 on this host (`/etc/rancher/rke2/rke2.yaml`, `~/.kube/rke2.yaml`, or
   `KUBECONFIG` pointing at an RKE2 config) → **rke2**: attach to the existing
   cluster and ADOPT its workloads — never install (zarf/converge owns them)
3. otherwise → **k3d**: provision a local `cybersec` k3d cluster (podman) and
   install Dask operator + JupyterHub from the bundled charts in `zarf/charts/`
   (no helm-repo downloads)

Set `CYBERSEC_K8S_TARGET=none` to run the core stack only. The kubeconfig for
whichever target is published at `.devenv/state/kubeconfig`.

The `k8s:*` tasks below remain for manual/CI control of the same components.

**Target Preparation** (validates requirements before deployment):
```bash
devenv tasks run k8s:prepare           # Auto-detect and prepare target
devenv tasks run k8s:prepare-aws       # AWS RKE2 with Dask/JupyterHub
devenv tasks run k8s:prepare-rke2      # Local RKE2 cluster
devenv tasks run k8s:prepare-k3d       # Local k3d development
```

**Cluster Provisioning**:
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
devenv tasks run k8s:prepare-rke2      # Validate and prepare RKE2 target
devenv tasks run k8s:deploy-dask       # Deploy to RKE2
```

### K8s Target Validation

The `k8s:prepare-*` tasks use Conftest policies to validate requirements:

| Target | Validates |
|--------|-----------|
| `aws` | AWS creds, ngrok, Cloudflare, SSH key, tofu/terraform |
| `rke2` | KUBECONFIG points to RKE2, cluster reachable |
| `k3d` | k3d installed, container runtime (podman/docker) |

CLI/MCP commands:
```bash
cybersec "/k8s"                    # Show status and detected target
cybersec "/k8s validate aws"       # Validate AWS target
cybersec "/k8s prepare k3d"        # Prepare k3d target
cybersec "/k8s prepare aws --dry-run"  # Validate without writing config
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

### Data Flow (Java Pipeline - Default)
```
Java Flink DataGen → Iceberg Table → Polaris REST Catalog → MinIO S3
```

The Java pipeline (`CloudTrailDataGenIcebergJob`) is the default for benchmarking. It generates synthetic CloudTrail events and writes directly to Iceberg at 100 rows/sec.

### Data Flow (Python Pipeline - Disabled by default)
```
PyFlink DataGen → Iceberg Table → Polaris REST Catalog → MinIO S3
```

The Python pipeline (`flink_jobs/cloudtrail_datagen.py`) generates synthetic CloudTrail events and writes directly to Iceberg at 10 rows/sec. It is independent from the Java pipeline - both can run simultaneously.

To enable: set `disabled = false` on `cloudtrail-datagen` in devenv.nix.

### Key Components

**Python Pipeline** (root directory):
- `flink_jobs/cloudtrail_datagen.py` - Generates synthetic CloudTrail events, writes to Iceberg
- `iceberg_writer/cloudtrail_writer.py` - Iceberg table utilities
- `iceberg_writer/cloudtrail_query.py` - Python query interface
- `iceberg_browser.py` - Flask web UI for browsing Iceberg data
- `main.py` - Pipeline status and utilities

**Java Toolkit** (`flink-cyber/`):
- `flink-common/` - Iceberg integration including `CloudTrailDataGenIcebergJob` (default datagen)
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
- Apache NiFi 2.0.0 (in `thirdparty/nifi/`) for data flow visualization
- Automatic Polaris bootstrap and catalog initialization

## Health Framework Design Principles

### 1. Root Cause Detection, Not Service Restarts

**The health framework detects ROOT CAUSES that prevent services from starting. Service lifecycle is devenv/process-compose's job.**

| Wrong Approach | Right Approach |
|----------------|----------------|
| "Flink not running" → Start Flink | "Shared memory limits too low" → Fix limits |
| "PostgreSQL down" → Restart PostgreSQL | "SHMMNI/SHMMAX exhausted" → Increase kernel limits |
| "NiFi not responding" → Start NiFi | "NiFi not installed" → Build from submodule |

Example from process-compose logs showing root cause:
```
FATAL: could not create shared memory segment: No space left on device
HINT: all available shared memory IDs have been taken, raise SHMMNI...
```

This leads to `SYSTEM_001` which **proactively detects** low `kern.sysv.shmmax/shmall` BEFORE services try to start, rather than waiting for PostgreSQL to fail.

**Avoid superficial "service not running" checks** that duplicate what `devenv tasks run restart:clean` already handles.

### 2. Submodule-First Builds

**All components MUST prefer building from `thirdparty/` submodules over downloading binaries.**

This applies to:
- **Health checks** (`/health`): Verify submodule initialization before component availability
- **Self-healing fixes** (`/health fix`): Build from source, not download
- **AIOps automation** (rete rules, heuristics): Submodule-aware detection and remediation
- **Bootstrap system**: Initialize and build submodules automatically

### Submodule Layout

| Component | Submodule Path | Build Tool |
|-----------|----------------|------------|
| Flink | `thirdparty/flink` | Maven (`mvn install -DskipTests -Dfast`) |
| PyFlink | `thirdparty/flink/flink-python` | uv editable install |
| Iceberg | `thirdparty/iceberg` | Gradle (`gradlew shadowJar`) |
| NiFi | `thirdparty/nifi` | Maven (`mvn install -DskipTests`) |
| Polaris | `thirdparty/polaris` | Gradle (`gradlew assemble`) |

### Benefits
1. **Reproducible builds** tied to git commits
2. **Consistent versions** across developer machines
3. **Patch capability** for customizations
4. **No external downloads** during development

### Implementation Notes
When implementing new health checks, fixes, or automation:
- Check for submodule initialization (`pom.xml`, `build.gradle`, `setup.py`)
- Build from source before falling back to alternatives
- Update remediation messages to reference submodule builds
- Test on both fresh clones and existing checkouts

## Key Configuration

### Versions (from `flink-cyber/pom.xml`)
- Flink: 1.20.1 (Apache, built from source)
- Iceberg: 1.9.0
- Java: 1.8 for compilation
- Scala: 2.12

### Environment Variables
- `JAVA_DATAGEN_RPS`: Rows per second for Java datagen (default: `100`)
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

# K8s target commands
/k8s                       # Show status and detected target
/k8s validate aws          # Validate AWS target
/k8s prepare k3d           # Prepare k3d target
/k8s prepare aws --dry-run # Validate without writing config

# AWS commands (pre-flight validation)
/aws                       # Show developer identity and config
/aws preflight             # Quota validation (EIPs, VPCs) before deployment
/aws preflight us-west-1   # Check quotas in specific region
/aws preflight --eips      # List current EIP allocations
/aws target us-west-1      # Set target region

# Zarf air-gap deployment commands
/zarf                      # Show package info and status
/zarf preflight            # Validate air-gap deployment requirements
/zarf preflight --registry # Include registry connectivity check
/zarf package              # Build Zarf package
/zarf deploy               # Deploy to cluster
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
