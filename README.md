# Cybersec Toolkit

[![Build and Test](https://github.com/cloudera/cybersec/actions/workflows/build_and_test.yml/badge.svg)](https://github.com/cloudera/cybersec/actions/workflows/build_and_test.yml)

## Overview
Enterprises deploy many point solutions to defend their networks.  These point solutions provide a wealth of data about the enterprise assets and networks but it is difficult to provide analytics on this data because there is no common repository and the events are in different formats.  The Cybersec Toolkit is a pipeline that ingests, correlates and prepares cybersecurity data for analytics.  The Cyber Toolkit leverages the Cloudera Data Platform to build a Security Data Lakehouse.

The Cyber Toolkit ingests raw log events from a variety of sources, parses and normalizes the log events using a common schema, enriches the events with reference data, scores the log events, profiles the events, and streams the events to a Kafka and a data lakehouse.   Integrate with orchestration or investigation and ticketing platforms using Flink SQL (SQL Stream Builder) on the triaged event topic.  Query the data lakehouse using SQL for visualizations and ad hoc queries or Spark for notebooks, investigations and machine learning model training.

The Cyber Toolkit is flexible and configurable so the ingestion can be changed with low or no code.
 
## Ingestion Stages
1. [Parse](flink-cyber/parser-chains-flink/README.md)
2. [Triage](flink-cyber/flink-enrichment/flink-enrichment-combined/README.md)
3. [Index](flink-cyber/flink-indexing/flink-indexing-hive/README.md)
4. [Profile](flink-cyber/flink-profiler-java/README.md)

## Tools
1. [Batch Enrichment Load](flink-cyber/flink-enrichment/flink-enrichment-load/README.md)
2. [Upsert Scoring Command](flink-cyber/flink-commands/scoring-commands/README.md)
3. [Event Generation](flink-cyber/caracal-generator/README.md)

## Packaging
The Cybersec Toolkit includes a Cloudera Manager parcel and service for easier installation.

Artifacts are available for download on the [releases page](https://github.com/cloudera/cybersec/releases).
You can also find less stable, but more up to date artifacts by selecting one of successful runs on [this page](https://github.com/cloudera/cybersec/actions/workflows/publish_release.yml) and scrolling to the bottom of the selected run page.

Or you can find artifacts after the build in the following directories:
1. [Parcel](flink-cyber/cyber-parcel)
2. [Cloudera Service](flink-cyber/cyber-csd)

## Building from Source
### Clone repo
```
git clone https://github.com/cloudera/cybersec.git
```

### Build with tests

```
cd cybersec/flink-cyber
mvn clean install
```

### Build without running tests
```
cd cybersec/flink-cyber
mvn clean install -DskipTests
```

## Local Development Environment

This repository includes a complete local development environment using [devenv](https://devenv.sh/).

### Quick Start

```bash
# Clone and enter devenv shell
git clone --recursive https://github.com/cloudera/cybersec.git
cd cybersec
devenv shell

# Install Python dependencies and CLI
uv sync
uv pip install -e .

# Start services (auto-bootstraps on first run)
devenv tasks run restart:clean

# Verify environment
cybersec "/bootstrap status"
```

On first run, `restart:clean` automatically:
- Initializes git submodules (if not cloned with `--recursive`)
- Builds Flink from source (~10-15 minutes)
- Downloads NiFi binary (~2 minutes on macOS)
- Starts all services (PostgreSQL, Polaris, MinIO, Flink, NiFi, etc.)

### CLI Usage

The `cybersec` CLI provides unified commands that work identically across CLI, MCP, and TUI:

```bash
# Health diagnostics
cybersec "/health"                    # FMEA-based health check
cybersec "/health flink"              # Flink/PyFlink diagnostics
cybersec "/health nifi"               # NiFi diagnostics
cybersec "/health diagnose FLINK_001" # Diagnose specific issue

# Fix detected issues
cybersec "/health fix"                # Dry-run (show what would be fixed)
cybersec "/health fix --apply"        # Apply fixes

# Bootstrap and configuration
cybersec "/bootstrap status"          # Check service health
cybersec "/bootstrap info"            # Show configuration
cybersec "/bootstrap verify"          # Verify environment

# JSON output for scripting
cybersec "/health flink --json"
```

### Services

| Service | URL | Description |
|---------|-----|-------------|
| Apache Flink | http://localhost:8081 | Job manager UI |
| Iceberg Browser | http://localhost:5050 | Data lakehouse browser |
| MinIO Console | http://localhost:9011 | S3 storage (minioadmin/minioadmin) |
| Apache Polaris | http://localhost:8181 | Iceberg REST catalog |
| PostgreSQL | localhost:5438 | Catalog database |
| Prometheus | http://localhost:9090 | Metrics |
| NiFi | http://localhost:8450 | Data flow visualization |
| OTEL Collector | localhost:4317/4318 | Telemetry (gRPC/HTTP) |

### Key Tasks

```bash
devenv tasks run restart:clean  # Clean restart (auto-bootstraps on fresh clone)
devenv tasks run polaris:check  # Verify Polaris configuration
devenv tasks run docs:build     # Build documentation
```

### MCP Integration

For AI-assisted development, the cybersec MCP server provides the same commands:

```python
# In Claude Code or other MCP clients
cmd("/health flink")
cmd("/bootstrap status --json")
```

See [CLAUDE.md](CLAUDE.md) for detailed development instructions.

