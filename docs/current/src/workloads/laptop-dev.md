# Laptop Development

Develop and test Flink pipelines on your laptop using devenv-managed services.

## Overview

The laptop development environment provides:

- Local Flink execution (no cluster required)
- PostgreSQL + MinIO + Polaris for Iceberg
- Optional K3d for Kubernetes testing
- Full observability stack (OTEL + Prometheus)

## Environment Setup

```bash
# Start core services
devenv up

# Verify health
cybersec bootstrap status
```

### Service Ports

| Service | Port | URL |
|---------|------|-----|
| PostgreSQL | 5438 | - |
| MinIO API | 9010 | - |
| MinIO Console | 9011 | [localhost:9011](http://localhost:9011) |
| Polaris REST | 8181 | - |
| Polaris Admin | 8182 | - |
| Flink Web UI | 8081 | [localhost:8081](http://localhost:8081) |
| Iceberg Browser | 5050 | [localhost:5050](http://localhost:5050) |
| Prometheus | 9090 | [localhost:9090](http://localhost:9090) |

## Typical Workflow

1. **Write pipeline code** in `flink_jobs/`
2. **Run locally** with `uv run python flink_jobs/your_job.py`
3. **Check Iceberg Browser** for data landing
4. **Query with Python** using `iceberg_writer/cloudtrail_query.py`

## When to Use K3d

Add K3d when you need:

- Dask cluster for distributed compute
- JupyterHub for notebook workflows
- Testing Kubernetes manifests before RKE2

```bash
# Provision K3d cluster
devenv tasks run k8s:provision

# Deploy Dask
devenv tasks run k8s:deploy-dask

# Access Dask dashboard
devenv tasks run k8s:forward
# → http://localhost:8787
```

## Subchapters

- [Local Flink Environment](./laptop-dev/flink-local.md): Flink setup and job execution
- [K3d for Testing](./laptop-dev/k3d.md): Kubernetes on laptop
- [Scenarios](./laptop-dev/scenarios.md): BDD scenarios for this workload
