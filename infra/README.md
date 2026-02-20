# Infrastructure Management

Automation for deploying the Cybersec Security Data Lakehouse across local and cloud environments. The platform consists of two workload clusters — **Dask/JupyterHub** for interactive analytics and **Flink/NiFi** for stream processing — with a security log pipeline (CloudTrail + VPC Flow Logs) feeding data between them.

## Deployment Strategies

```mermaid
flowchart TD
    subgraph strategies["Choose Your Deployment Strategy"]
        direction LR
        local["**Local Dev**\nk3d on Podman\n~5 min · Free"]
        hybrid["**Hybrid**\nFlink/NiFi on-prem\nDask/JupyterHub on AWS"]
        cloud["**Full Cloud**\nBoth clusters on AWS\nDual RKE2"]
    end

    local --> zarf_local["zarf package deploy\n(k3d cluster)"]
    hybrid --> devenv_onprem["devenv up\n(Flink/NiFi local)"]
    hybrid --> tofu_dask["aws:provision\n(Dask cluster only)"]
    cloud --> tofu_both["aws:provision\n(enable_flink_cluster=true)"]

    tofu_dask --> ansible_dask["Ansible: RKE2 + Zarf deploy"]
    tofu_both --> ansible_both["Ansible: RKE2 + Zarf deploy\n(both clusters)"]

    style local fill:#e8f5e9
    style hybrid fill:#fff3e0
    style cloud fill:#e3f2fd
```

### Strategy Comparison

| | Local Dev | Hybrid | Full Cloud |
|---|---|---|---|
| **Dask/JupyterHub** | k3d (Zarf) | AWS RKE2 (Zarf) | AWS RKE2 (Zarf) |
| **Flink/NiFi** | devenv up | devenv up (on-prem) | AWS RKE2 (Ansible) |
| **Security Logs** | Synthetic (devenv) | CloudTrail + VPC Flow Logs | CloudTrail + VPC Flow Logs |
| **Infra Provisioning** | `k8s:provision` | `aws:provision` | `aws:provision` |
| **App Deployment** | `zarf package deploy` | `zarf package deploy` + devenv | `zarf package deploy` + Ansible |
| **External Access** | localhost port-forward | Cloudflare Tunnel | Cloudflare Tunnel |
| **Cost** | Free | AWS (Dask cluster only) | AWS (both clusters) |

## Architecture

### Full Cloud (Dual-Cluster on AWS)

```mermaid
graph TB
    subgraph cf["Cloudflare Zero Trust"]
        tunnel["Cloudflare Tunnel\n(WARP device posture)"]
    end

    subgraph vpc["AWS VPC 10.100.0.0/16"]
        subgraph pub["Public Subnets"]
            bastion["Bastion\nt3.small"]
        end

        subgraph priv["Private Subnets"]
            subgraph dask_cluster["Dask/JupyterHub Cluster"]
                dask_cp["Control Plane\nm6i.xlarge × 1"]
                dask_w["Workers\nr6i.xlarge × 8\n(32 GiB RAM each)"]
                dask_nlb["NLB\ncybersec-dask-k8s-api"]
            end

            subgraph flink_cluster["Flink/NiFi Cluster"]
                flink_cp["Control Plane\nm6i.xlarge × 1"]
                flink_w["Workers\nc6i.xlarge × 2\n(8 GiB RAM each)"]
                flink_nlb["NLB\ncybersec-flink-k8s-api"]
            end
        end

        subgraph endpoints["VPC Endpoints"]
            s3gw["S3 Gateway\n(free)"]
            ssm["SSM\nInterface"]
        end

        subgraph s3["S3 Buckets"]
            data["cybersec-PREFIX-data\n(Iceberg warehouse, OTEL)"]
            logs["cybersec-PREFIX-security-logs\n(CloudTrail, VPC Flow Logs)"]
        end
    end

    tunnel --> bastion
    tunnel --> dask_cp
    tunnel --> flink_cp
    bastion --> dask_cp
    bastion --> flink_cp
    dask_w --> s3gw --> data
    flink_w --> s3gw --> logs

    subgraph ct["CloudTrail + VPC Flow Logs"]
        trail["Management Events"]
        flowlog["All Traffic"]
    end
    trail --> logs
    flowlog --> logs
```

### Hybrid (On-Prem Flink + AWS Dask)

```mermaid
graph TB
    subgraph onprem["On-Premises (devenv up)"]
        flink_local["Flink 1.20.1\n(stream processing)"]
        nifi_local["NiFi 2.0.0\n(data flow)"]
        polaris["Polaris REST Catalog"]
        minio["MinIO\n(local S3)"]
        pg["PostgreSQL 16"]
        otel["OTEL Collector"]
        prom["Prometheus"]
    end

    subgraph vpc["AWS VPC"]
        subgraph dask_cluster["Dask/JupyterHub Cluster (RKE2)"]
            dask_cp["Control Plane"]
            dask_w["Workers × 8"]
        end
        s3data["S3: cybersec-PREFIX-data"]
        s3logs["S3: cybersec-PREFIX-security-logs"]
    end

    flink_local --> minio
    flink_local --> polaris
    dask_w --> s3data
    s3logs -.->|"Flink FileSource\n(FLIP-27)"| flink_local

    subgraph cf["Cloudflare Tunnel"]
        tunnel["Zero Trust\nWARP required"]
    end
    tunnel --> dask_cp
```

### Security Log Pipeline

```mermaid
flowchart LR
    subgraph sources["AWS Event Sources"]
        ct["CloudTrail\n(API calls)"]
        fl["VPC Flow Logs\n(network traffic)"]
    end

    subgraph s3["S3 Bucket"]
        ct_path["AWSLogs/.../CloudTrail/\n*.json.gz"]
        fl_path["AWSLogs/.../vpcflowlogs/\n*.log.gz"]
    end

    subgraph flink["Flink Cluster"]
        fs["FileSource\n(FLIP-27)\nmonitors S3 prefixes"]
        proc["Stream Processing\n(enrich, correlate)"]
    end

    subgraph iceberg["Iceberg Tables"]
        lake["Security Data\nLakehouse"]
    end

    ct --> ct_path
    fl --> fl_path
    ct_path --> fs
    fl_path --> fs
    fs --> proc --> lake

    subgraph alarm["CloudWatch"]
        cw["Delivery Alarm\n(no events in 1hr)"]
    end
    ct -.-> cw
```

## Zarf Air-Gap Deployment

All Dask/JupyterHub deployments use the **cybersec-dask** Zarf package, regardless of target environment. This ensures identical workloads across local dev, hybrid, and full cloud.

```mermaid
flowchart TD
    subgraph build["Build (internet access)"]
        pkg["zarf package create\n→ cybersec-dask-amd64-1.1.1.tar.zst"]
    end

    subgraph transfer["Transfer to Air-Gap"]
        scp["scp / USB / S3"]
    end

    subgraph deploy["Deploy (no internet needed)"]
        init["zarf init\n(internal registry)"]
        app["zarf package deploy\n(cybersec-dask)"]
    end

    subgraph k8s["RKE2 Cluster"]
        op["Dask Operator"]
        dc["DaskCluster\n(scheduler + workers)"]
        jh["JupyterHub"]
        pv["Panel-Viz\n(OTEL Navigator)"]
        eng["Navigator Engine\n(gRPC)"]
        nb["Sample Notebooks"]
        ing["Ingress"]
    end

    pkg --> scp --> init --> app
    app --> op & dc & jh & pv & eng & nb & ing
```

### Zarf Package Components

| Component | Required | Description |
|---|---|---|
| `cybersec-images` | Yes | Custom Dask image with Panel, HoloViews, pyarrow |
| `dask-operator` | Yes | Helm chart: Dask Kubernetes Operator |
| `dask-cluster` | Yes | DaskCluster CRD (scheduler + workers) |
| `jupyterhub` | No (default: on) | Helm chart: JupyterHub with Dask integration |
| `panel-viz` | No (default: on) | OTEL Navigator dashboard |
| `navigator-engine` | No (default: on) | gRPC engine for terminal-driven analytics |
| `sample-notebooks` | No (default: on) | Pre-loaded Jupyter notebooks |
| `ingress` | No (default: on) | Traefik ingress resources |

## Quick Start

### Local Development (k3d)

```bash
devenv tasks run k8s:provision       # Create k3d cluster
cd zarf && zarf package deploy       # Deploy Dask/JupyterHub
devenv tasks run k8s:forward         # Port-forward services
```

### Hybrid (Flink on-prem + Dask on AWS)

```bash
# Terminal 1: Local Flink/NiFi stack
devenv up

# Terminal 2: AWS Dask cluster
devenv tasks run aws:provision       # OpenTofu: VPC, EC2, S3
devenv tasks run aws:deploy          # Ansible: RKE2 + Zarf package
```

### Full Cloud (Both clusters on AWS)

```bash
export TF_VAR_enable_flink_cluster=true
export TF_VAR_enable_security_logs=true

devenv tasks run aws:provision       # OpenTofu: both clusters + security pipeline
devenv tasks run aws:deploy          # Ansible: RKE2 + Zarf on Dask cluster
                                     # Ansible: RKE2 + Flink/NiFi on Flink cluster
```

## Task Reference

### `k8s:*` — Local and Existing Cluster Deployment

| Task | Description |
|---|---|
| `k8s:prepare` | Auto-detect and validate target (k3d, RKE2, AWS) |
| `k8s:provision` | Create k3d cluster (k3d target only) |
| `k8s:deploy-dask` | Deploy Dask operator + cluster via Helm |
| `k8s:deploy-jupyter` | Deploy JupyterHub via Helm |
| `k8s:forward` | Port-forward Dask (8787), JupyterHub (8000), K8s Dashboard (10443) |
| `k8s:status` | Check cluster connectivity and deployed resources |
| `k8s:destroy` | Delete k3d cluster (k3d target only) |

### `aws:*` — AWS Cloud Deployment

| Task | Description |
|---|---|
| `aws:provision` | OpenTofu: VPC, EC2, IAM, S3, Cloudflare, security pipeline |
| `aws:destroy` | Tear down all AWS infrastructure (auto-empties S3 buckets) |
| `aws:teardown` | Full teardown with OPA policy validation + S3 cleanup |
| `aws:deploy` | Ansible: RKE2 bootstrap + Zarf package deploy |
| `aws:ssh` | SSH to bastion host via Cloudflare Tunnel |
| `aws:status` | Show infrastructure and service status |
| `aws:s3:clean` | Remove objects from S3 data bucket |

### Preflight Validation

| Task | Description |
|---|---|
| `aws:preflight` | IAM permissions, EIP/VPC quotas, security log access |
| `k8s:prepare-aws` | Validate AWS target (creds, SSH key, Cloudflare, tofu) |
| `k8s:prepare-rke2` | Validate existing RKE2 cluster |
| `k8s:prepare-k3d` | Validate k3d prerequisites (container runtime) |

## AWS Resource Naming

Symmetric naming convention with `cybersec` as the umbrella project:

```mermaid
graph LR
    subgraph shared["Shared Resources"]
        bastion["cybersec-bastion"]
        vpc["cybersec-vpc"]
        s3d["cybersec-PREFIX-data"]
        s3l["cybersec-PREFIX-security-logs"]
        iam["cybersec-rke2-node (IAM)"]
        tunnel["cybersec-PREFIX (CF Tunnel)"]
    end

    subgraph dask["Dask Cluster"]
        dcp["cybersec-dask-control-plane-N"]
        dw["cybersec-dask-worker-N"]
        dnlb["cybersec-dask-k8s-api"]
        dsg["cybersec-dask-control-plane (SG)\ncybersec-dask-worker (SG)"]
    end

    subgraph flink["Flink Cluster"]
        fcp["cybersec-flink-control-plane-N"]
        fw["cybersec-flink-worker-N"]
        fnlb["cybersec-flink-k8s-api"]
        fsg["cybersec-flink-control-plane (SG)\ncybersec-flink-worker (SG)"]
    end
```

## Network Topology

```mermaid
flowchart TB
    inet(("Internet"))

    subgraph vpc["VPC 10.100.0.0/16"]
        subgraph pub["Public Subnets (10.100.101-102.0/24)"]
            bastion["Bastion + cloudflared"]
            igw["Internet Gateway"]
        end

        subgraph priv["Private Subnets (10.100.1-2.0/24)"]
            dask_nodes["Dask: 1 CP + 8 workers"]
            flink_nodes["Flink: 1 CP + 2 workers"]
        end

        subgraph ep["VPC Endpoints"]
            s3_ep["S3 Gateway (free)"]
            ssm_ep["SSM Interface"]
            ecr_ep["ECR Interface\n(soft air-gap only)"]
        end
    end

    inet <--> igw <--> bastion
    bastion --> dask_nodes
    bastion --> flink_nodes
    dask_nodes --> s3_ep
    flink_nodes --> s3_ep
    dask_nodes -.-> ssm_ep
    flink_nodes -.-> ssm_ep

    nat{"NAT Gateway\n(soft air-gap only)"}
    igw --> nat --> priv

    style nat stroke-dasharray: 5 5
    style ecr_ep stroke-dasharray: 5 5
```

### Air-Gap Modes

| Mode | NAT Gateway | ECR Endpoints | Image Source | S3 Access |
|---|---|---|---|---|
| **Soft air-gap** (default) | Yes | Yes | ECR via VPC endpoint | S3 Gateway endpoint |
| **True air-gap** (`airgap_mode=true`) | No | No | Zarf internal registry | S3 Gateway endpoint |

## Service Ports

### Kubernetes Stack (Zarf-deployed)

| Service | Port | URL | NodePort (air-gap) |
|---|---|---|---|
| Dask Dashboard | 8787 | http://localhost:8787 | 30087 |
| Dask Scheduler | 8786 | (internal) | 30086 |
| JupyterHub | 8000 | http://localhost:8000 | 30080 |
| Panel-Viz | 5006 | (via ingress) | 30506 |
| K8s Dashboard | 10443 | https://localhost:10443 | — |

### Core Stack (devenv up)

| Service | Port | URL |
|---|---|---|
| Flink UI | 8081 | http://localhost:8081 |
| Polaris REST | 8181 | http://localhost:8181 |
| MinIO Console | 9011 | http://localhost:9011 |
| PostgreSQL | 5438 | (internal) |
| Prometheus | 9090 | http://localhost:9090 |
| NiFi | 8450 | http://localhost:8450 |
| OTEL Collector | 4317/4318 | gRPC / HTTP |

### Cloudflare Tunnel (AWS deployments)

| Service | FQDN |
|---|---|
| Bastion SSH | `bastion.dev.aws.zndx.org` |
| Dask Dashboard | `dask.dev.aws.zndx.org` |
| JupyterHub | `jupyter.dev.aws.zndx.org` |
| K8s Dashboard | `k8s.dev.aws.zndx.org` |
| Panel-Viz | `viz.dev.aws.zndx.org` |
| Flink UI | `flink.dev.aws.zndx.org` |
| NiFi | `nifi.dev.aws.zndx.org` |

## Directory Structure

```
infra/
├── README.md                  # This file
├── LOCAL.md                   # Local k3d and RKE2 workflows
├── aws/
│   ├── README.md              # AWS deployment guide
│   ├── tofu/                  # OpenTofu modules
│   │   ├── ec2.tf             # Dask cluster: CP, workers, NLB
│   │   ├── flink-cluster.tf   # Flink cluster: CP, workers, NLB, SGs
│   │   ├── security.tf        # Dask cluster security groups
│   │   ├── vpc.tf             # VPC, subnets, endpoints, air-gap config
│   │   ├── s3.tf              # Data bucket
│   │   ├── cloudtrail.tf      # Security logs bucket, CloudTrail, VPC Flow Logs
│   │   ├── cloudflare.tf      # Tunnel, DNS, Zero Trust access
│   │   └── variables.tf       # All configuration with defaults
│   └── ansible/               # Ansible playbooks
│       └── roles/
│           ├── rke2-server/    # RKE2 control plane bootstrap
│           ├── rke2-agent/     # RKE2 worker join
│           ├── zarf-deploy/    # Zarf init + package deploy
│           ├── dask/           # Dask operator (Helm fallback)
│           ├── jupyterhub/     # JupyterHub (Helm fallback)
│           ├── panel-viz/      # Panel-Viz dashboard
│           ├── datagen/        # OTEL data generation
│           ├── s3-data/        # S3 data seeding
│           ├── cloudflare-tunnel/ # cloudflared connector
│           ├── ngrok/          # ngrok ingress (alternative)
│           └── common/         # Base packages
├── benchmarks/
│   └── benchmark-job.yaml     # Dask benchmark K8s Job
└── dask/
    └── dask-cluster.yaml      # DaskCluster manifest (local)

zarf/
├── zarf.yaml                  # Package definition (cybersec-dask v1.1.1)
├── charts/                    # Helm charts (Dask operator, JupyterHub)
├── manifests/                 # K8s manifests (DaskCluster, Panel-Viz, Engine)
├── images/                    # Dockerfile + Python app code
└── scripts/                   # Build, deploy, and validation scripts
```

## Next Steps

- **Local development**: [LOCAL.md](LOCAL.md) — k3d or RKE2 workflows
- **AWS deployment**: [aws/README.md](aws/README.md) — Full cloud deployment guide
- **Health diagnostics**: `/health` — FMEA-based checks and auto-remediation
- **Policy validation**: `/k8s validate aws` — Conftest-based preflight checks
