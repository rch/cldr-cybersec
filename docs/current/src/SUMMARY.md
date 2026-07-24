# Summary

# Cyberphy

- [Introduction](./introduction.md)
- [Quick Start](./quickstart.md)

# Delivery (primary)

- [Zarf air-gap releases](./delivery/zarf.md)
- [AWS & infrastructure](./delivery/infra.md)
- [Converge & verification](./delivery/converge.md)

# Platform

- [System architecture](./architecture/overview.md)
    - [Data flow](./architecture/data-flow.md)
    - [Storage strategy](./architecture/storage.md)
    - [HDF5 ↔ Iceberg metadata](./architecture/hdf5-iceberg-metadata-plane.md)
- [Workloads overview](./workloads/overview.md)
    - [Laptop development](./workloads/laptop-dev.md)
        - [Local Flink](./workloads/laptop-dev/flink-local.md)
        - [K3d](./workloads/laptop-dev/k3d.md)
        - [Scenarios](./workloads/laptop-dev/scenarios.md)
    - [Workstation](./workloads/workstation.md)
        - [Flink toolkit](./workloads/workstation/flink-cyber.md)
        - [RKE2](./workloads/workstation/rke2.md)
        - [GPU workflows](./workloads/workstation/gpu.md)
        - [Scenarios](./workloads/workstation/scenarios.md)
    - [Benchmarking](./workloads/benchmarking.md)
        - [Telemetry](./workloads/benchmarking/telemetry.md)
        - [AWS RKE2](./workloads/benchmarking/aws-rke2.md)
        - [Dask + JupyterHub](./workloads/benchmarking/dask-jupyter.md)
        - [Scenarios](./workloads/benchmarking/scenarios.md)

# Architecture (depth)

- [AWS integration](./architecture/aws.md)
    - [CloudTrail (legacy reference)](./architecture/aws/cloudtrail.md)
    - [S3 table buckets](./architecture/aws/s3-tables.md)
    - [Glacier archival](./architecture/aws/glacier.md)
- [On-prem cluster](./architecture/onprem.md)
    - [Replication](./architecture/onprem/replication.md)
    - [Table optimization](./architecture/onprem/optimization.md)
- [Complex systems integration](./architecture/integration.md)

# Operations

- [Operations guide](./operations/overview.md)
    - [Health diagnostics](./operations/health.md)
    - [Bootstrap](./operations/bootstrap.md)
    - [Air-gap deployment](./operations/airgap-deployment.md)
    - [Runbook: air-gap update](./operations/runbook-airgap-update.md)
    - [Multi-RKE2 isolation](./operations/multi-rke2-isolation.md)
    - [Testing](./operations/testing.md)
    - [Historical retrieval](./operations/historical.md)
    - [Data verification](./operations/verification.md)

# Roadmap

- [Roadmap](./roadmap.md)

# Reference

- [Configuration](./reference/configuration.md)
- [Polaris setup](./reference/polaris.md)
- [Troubleshooting](./reference/troubleshooting.md)
