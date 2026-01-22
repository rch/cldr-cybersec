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
devenv up  # Start all services (Polaris catalog initializes automatically)
```

That's it! All services start and configure themselves automatically:
- PostgreSQL initializes with required databases
- Polaris starts and creates the catalog with proper permissions
- MinIO, Flink, and Iceberg Browser become available

### Verification

```bash
devenv tasks run polaris:check  # Verify Polaris configuration
```

### Clean Restart

```bash
devenv tasks run restart:clean  # Stop all processes and restart
```

### Services

- **Apache Flink**: Job management at http://localhost:8081
- **Iceberg Browser**: CloudTrail events UI at http://localhost:5050
- **MinIO Console**: Object storage at http://localhost:9011 (minioadmin/minioadmin)
- **Apache Polaris**: REST catalog at http://localhost:8181
- **PostgreSQL**: Metadata storage on port 5438

### Key Tasks

- `polaris:check` - Verify Polaris is properly configured
- `polaris:init` - Manually re-initialize Polaris (if needed)
- `restart:clean` - Clean restart of all services
- `docs:build` - Build documentation

**Note**: Polaris initialization happens automatically on `devenv up`. The `polaris:init` task is only needed if automatic initialization fails.

See [docs/POLARIS_SETUP.md](docs/POLARIS_SETUP.md) for detailed Polaris configuration and troubleshooting.

