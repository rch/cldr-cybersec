# Benchmarking & Multi-Cluster

Performance testing with local Flink sending telemetry to remote RKE2 on AWS with Dask and JupyterHub.

## Overview

The benchmarking workload enables:

- Local Flink cluster generating load
- Telemetry collection via OpenTelemetry
- Remote RKE2 on AWS for distributed processing
- Dask + JupyterHub for interactive analytics
- Multi-user collaboration on shared datasets

## Architecture

```d2
direction: right

Local: {
  label: "Laptop/Workstation"
  style.fill: "#e8f5e9"

  flink: "Flink Jobs"
  otel: "OTEL Collector"
  metrics: "Local Prometheus"
}

Remote: {
  label: "AWS RKE2"
  style.fill: "#e3f2fd"

  rke2: "RKE2 Cluster"
  dask: "Dask Scheduler"
  jupyter: "JupyterHub"
  s3: "S3 Storage"
}

Local.otel -> Remote.rke2: "Telemetry\n(OTLP)"
Local.flink -> Remote.s3: "Data\n(Iceberg)"
Remote.jupyter -> Remote.dask: "Compute"
Remote.dask -> Remote.s3: "Read/Write"
```

## Environment Setup

### Local Side

```bash
# Start local services with telemetry
devenv up

# Verify OTEL collector
curl http://localhost:8889/metrics | head
```

### Remote RKE2 on AWS

```bash
# Set kubeconfig for remote cluster
export KUBECONFIG=~/.kube/rke2-aws.yaml

# Deploy Dask operator
devenv tasks run k8s:deploy-dask

# Deploy JupyterHub
devenv tasks run k8s:deploy-jupyter
```

### Configure Telemetry Export

```yaml
# otel-collector-config.yaml
exporters:
  otlp:
    endpoint: "rke2-otel.your-domain.com:4317"
    tls:
      insecure: false
```

## Benchmarking Workflow

1. **Generate load locally**: Run Flink jobs with synthetic data
2. **Collect telemetry**: OTEL captures metrics and traces
3. **Analyze remotely**: JupyterHub notebooks query Dask
4. **Compare results**: Cross-environment performance comparison

## Multi-User Collaboration

JupyterHub provides:

- Per-user persistent storage
- Shared datasets via Iceberg
- Dask cluster access for all users
- GPU scheduling (if available)

## Cost Considerations

| Component | Instance Type | Monthly Cost |
|-----------|---------------|--------------|
| RKE2 Control Plane | m6i.xlarge (3x) | ~$500 |
| Dask Workers | r6i.2xlarge (4x) | ~$1,200 |
| JupyterHub | m6i.large | ~$70 |
| S3 Storage | 1TB | ~$25 |
| Data Transfer | ~500GB/mo | ~$50 |

## Subchapters

- [Local Telemetry Collection](./benchmarking/telemetry.md): OTEL setup
- [Remote RKE2 on AWS](./benchmarking/aws-rke2.md): AWS infrastructure
- [Dask + JupyterHub](./benchmarking/dask-jupyter.md): Analytics stack
- [Scenarios](./benchmarking/scenarios.md): BDD scenarios for this workload
