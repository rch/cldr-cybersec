# Introduction

The Cybersec Toolkit provides a unified pipeline for ingesting, processing, and analyzing cybersecurity data across hybrid cloud environments. It enables security teams to work with CloudTrail logs, network telemetry, and security events using modern data lakehouse architecture.

## What Is Cybersec Toolkit?

A data platform that brings together:

- **Apache Flink** for real-time stream processing
- **Apache Iceberg** for unified table format across environments
- **Cloudera's Cyber Toolkit** for security-specific enrichment and analysis
- **Kubernetes (RKE2/K3d)** for scalable deployment
- **Dask + JupyterHub** for interactive analytics and ML workloads

## Who Is This For?

This documentation serves three primary workload patterns:

| Workload | Environment | Use Case |
|----------|-------------|----------|
| **Laptop Development** | Local Flink, optional K3d | Developing and testing pipelines |
| **Workstation + GPUs** | Flink, Cyber Toolkit, RKE2 | Security analysis with ML acceleration |
| **Benchmarking** | Local Flink → Remote RKE2/AWS | Performance testing with Dask/Jupyter |

## Core Principles

- **Iceberg everywhere**: Consistent table format whether data lives in S3, MinIO, or HDFS
- **Workload-first design**: Documentation organized around what you're trying to accomplish
- **Scenario-driven validation**: Features grounded in BDD scenarios that match real workflows
- **Hybrid by default**: Seamless operation across laptop, workstation, and cloud environments

## Technology Stack

| Component | Local (Dev) | Workstation | Cloud |
|-----------|-------------|-------------|-------|
| Streaming | Flink (devenv) | Flink + Cyber Toolkit | Cloudera Data Flow |
| Storage | MinIO | MinIO / Ozone | S3 / S3 Tables |
| Catalog | Polaris REST | Polaris REST | Iceberg REST |
| Compute | K3d (optional) | RKE2 | EKS / RKE2 |
| Analytics | Local Dask | Dask + GPU | Dask + JupyterHub |

## Getting Started

1. **[Quick Start](./quickstart.md)**: Get the environment running in 5 minutes
2. **[Workloads](./workloads/overview.md)**: Find your workload pattern and dive in
3. **[Architecture](./architecture/overview.md)**: Understand how the pieces fit together

## Document Structure

- **Platform Overview**: Introduction and quick start
- **Workloads**: Task-oriented guides for each deployment pattern
- **Architecture**: System design, data flow, and integration patterns
- **Operations**: Health checks, bootstrap, monitoring
- **Roadmap**: Future development timeline
- **Reference**: Configuration, setup guides, troubleshooting
