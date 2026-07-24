# Introduction

**Cyberphy** is a platform for **cyber-physical systems observability and analytics**: OpenTelemetry from the plant floor and robot cell, through stream processing, into a lakehouse you can query and explore—plus OTel from the platform itself.

This book documents how we **deploy** (Zarf air-gap releases, AWS OpenTofu + Ansible) and how the **stack** works (Flink, NiFi, Polaris, Iceberg, Dask, Panel).

## What we optimize for

| Priority | Path | Audience |
|----------|------|----------|
| **1 — Air-gap K8s** | [`zarf/`](https://github.com/weathership/cyberphy/tree/trunk/zarf) packages + converge | Operators on RKE2 without outbound pull |
| **2 — AWS / lab infra** | [`infra/`](https://github.com/weathership/cyberphy/tree/trunk/infra) tofu + ansible | Provision RKE2, stage Zarf, verify |
| **3 — Processing + UI** | Flink toolkit, Iceberg, Panel OTEL Navigator | Developers extending CPS event shapes |

## Domain shift (from security SIEM to CPS)

| Was (legacy cybersecurity framing) | Is (cyberphy) |
|------------------------------------|---------------|
| CloudTrail / network security logs as primary | **Manufacturing, robotics, industrial** messages as first-class events |
| Point-product security lakehouse | **OTel lakehouse** for CPS *and* platform self-telemetry |
| Cloudera Manager parcel / CSD install | **Zarf** + **infra/** only (CM packaging retired) |

Flink pipelines, NiFi flows, Iceberg tables, and the interactive Dask/Panel UI **remain**. Payloads and schemas evolve toward CPS; the engines stay.

## Data path (conceptual)

```text
  CPS devices / robots / PLCs          Platform (Flink, NiFi, Dask, UI)
           │                                        │
           │  OTel / domain events                  │  OTel self-telemetry
           ▼                                        ▼
     Ingest → Flink (normalize, enrich, score) → Iceberg (Polaris + S3/MinIO)
                         │
              Interactive: Panel / Jupyter / Dask
```

## Who this book is for

| Role | Start here |
|------|------------|
| Air-gap operator | [Zarf air-gap releases](./delivery/zarf.md), [Converge](./delivery/converge.md) |
| AWS / cluster provisioner | [AWS & infrastructure](./delivery/infra.md) |
| Pipeline / Flink developer | [Workloads](./workloads/overview.md), [Architecture](./architecture/overview.md) |
| Local laptop | [Quick Start](./quickstart.md), [Laptop development](./workloads/laptop-dev.md) |

## Repository

- **GitHub:** [weathership/cyberphy](https://github.com/weathership/cyberphy)  
- **Default branch:** `trunk`  
- **Published docs:** GitHub Pages (built from `docs/current` on push to `trunk`)

Historical cybersecurity packaging and CDP-centric install paths live on upstream remotes only; they are not delivery targets for this project.
