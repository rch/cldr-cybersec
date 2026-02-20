# AWS Infrastructure: Dual-Cluster + Security Log Pipeline

## Design Decision: Separate RKE2 Clusters

Flink/NiFi and Dask/JupyterHub are deployed on **separate RKE2 clusters** sharing
VPC, bastion, S3, and Cloudflare tunnel infrastructure.

**Rationale:**
- **Right-sized instances:** Dask workers need memory (r6i.xlarge, 32 GiB); Flink
  workers need compute (c6i.xlarge, 8 GiB) + fast local storage for RocksDB state
- **Independent scaling:** Add Flink capacity without affecting Dask, and vice versa
- **Blast radius:** Flink checkpoint storms don't impact Dask DataFrames
- **Independent lifecycle:** Upgrade Flink's K8s version without touching Dask

**Shared resources** (no duplication):
- VPC, subnets, IGW, NAT, VPC endpoints
- Bastion host + Cloudflare tunnel
- S3 data bucket + security-logs bucket
- IAM role + instance profile

## Design Decision: Flink FileSource over SQS

**Chosen:** Flink `FileSource` (FLIP-27) with `monitorContinuously()` watches
S3 prefixes for new files on a configurable polling interval.

**Rejected:** SQS-based event notification (S3 → SQS → Flink SQS connector).

**Rationale:**
- No SQS dependency — fewer moving parts
- No VPC Interface endpoint needed (~$7/mo per AZ)
- All S3 access through the existing **free** S3 Gateway VPC endpoint
- Works in true air-gap without additional infrastructure
- Trade-off: 1-5 min polling latency vs SQS sub-minute; acceptable for analytics

## Architecture

```
VPC 10.100.0.0/16 (shared)
├── Public Subnets
│   └── Bastion (t3.small) + cloudflared tunnel
│
├── Private Subnets
│   ├── Dask/JupyterHub RKE2 Cluster
│   │   ├── 1× m6i.xlarge control plane
│   │   ├── N× r6i.xlarge workers (memory-optimized, 500 GB data vol)
│   │   └── NLB → K8s API :6443
│   │
│   └── Flink/NiFi RKE2 Cluster (enable_flink_cluster=true)
│       ├── 1× m6i.xlarge control plane
│       ├── N× c6i.xlarge workers (compute-optimized, 200 GB data vol)
│       └── NLB → K8s API :6443
│
├── S3 (via Gateway VPC Endpoint — free)
│   ├── cybersec-dask-{prefix}-data          ← Shared data bucket
│   │   ├── otel/spans/...                    ← Flink OTel → Panel-Viz
│   │   └── iceberg/warehouse/                ← Flink → Iceberg processed output
│   └── cybersec-dask-{prefix}-security-logs  ← Raw security logs
│       └── AWSLogs/{account}/
│           ├── CloudTrail/.../*.json.gz      ← Flink FileSource #1
│           └── vpcflowlogs/.../*.log.gz      ← Flink FileSource #2
│
└── Cloudflare Tunnel (shared, single tunnel)
    ├── dask.dev.aws.zndx.org     → Dask cluster
    ├── jupyter.dev.aws.zndx.org  → Dask cluster
    ├── viz.dev.aws.zndx.org      → Dask cluster
    ├── k8s.dev.aws.zndx.org      → Dask cluster
    ├── flink.dev.aws.zndx.org    → Flink cluster (conditional)
    └── nifi.dev.aws.zndx.org     → Flink cluster (conditional)
```

## Changes

### New: `infra/aws/tofu/flink-cluster.tf`

- `aws_security_group.flink_control_plane` — K8s API, etcd, kubelet, VXLAN
- `aws_security_group.flink_worker` — includes Flink UI (8081) and NiFi (8450)
- `aws_instance.flink_control_plane` — m6i.xlarge, 100 GB root
- `aws_instance.flink_worker` — c6i.xlarge, 100 GB root + 200 GB data (RocksDB)
- `aws_lb.flink_k8s_api` — internal NLB for Flink K8s API
- Target groups + listeners for K8s API (6443) and RKE2 supervisor (9345)
- All resources gated on `var.enable_flink_cluster` (default: false)
- All tagged with `Cluster = "flink"` for Cloudcraft

### New: `infra/aws/tofu/cloudtrail.tf`

- `aws_s3_bucket.security_logs` — raw CloudTrail + VPC Flow Logs (90-day lifecycle)
- `aws_cloudtrail.trail` — account-level, management events + optional data events
- `aws_flow_log.vpc` — extended v5 fields for security analytics
- `aws_cloudwatch_metric_alarm.cloudtrail_delivery` — trail health
- All gated on `var.enable_security_logs` (default: true)

### Modified: `infra/aws/tofu/variables.tf`

Flink cluster:
- `enable_flink_cluster` (bool, default: false)
- `flink_control_plane_count` / `flink_control_plane_instance_type`
- `flink_worker_count` (default: 2) / `flink_worker_instance_type` (default: c6i.xlarge)
- `flink_root_volume_size` / `flink_data_volume_size` (default: 200 GB)

Security logs:
- `enable_security_logs` (bool, default: true)
- `cloudtrail_enable_data_events` / `cloudtrail_insight_types`
- `security_logs_retention_days` (default: 90)

Ingress:
- Added `flink` and `nifi` to `ingress_subdomains` and `ingress_domains`

### Modified: `infra/aws/tofu/ec2.tf`

- `aws_iam_role_policy.security_logs_read` — S3 read on security-logs bucket

### Modified: `infra/aws/tofu/s3.tf`

- Added `iceberg/warehouse/` prefix to data bucket

### Modified: `infra/aws/tofu/outputs.tf`

- Renamed `worker_*` → `dask_worker_*`
- Added `flink_control_plane_private_ips`, `flink_worker_private_ips`, `flink_k8s_api_endpoint`
- `cluster_info` now has `dask_workers`, `flink_cluster` sections
- Ansible inventory template receives both cluster groups

### Modified: `infra/aws/tofu/templates/inventory.tftpl`

- Renamed `[control_plane]` → `[dask_control_plane]`, `[workers]` → `[dask_workers]`
- Added `[dask_cluster:children]` with `cluster_name=dask`
- Added `[flink_control_plane]`, `[flink_workers]`, `[flink_cluster:children]` (conditional)
- `[rke2:children]` includes both clusters for shared provisioning

### Modified: `infra/aws/tofu/cloudflare.tf`

- Dynamic `ingress_rule` blocks for Flink UI and NiFi UI (conditional)
- DNS records for `flink.dev.aws.zndx.org` and `nifi.dev.aws.zndx.org` (conditional)
- Access application destinations include Flink/NiFi domains
- `ingress_urls` output includes Flink/NiFi when enabled

## Cloudcraft Visibility

All resources tagged with `common_tags` + workload-specific tags:
- `Cluster = "flink"` on all Flink cluster resources
- `Pipeline = "security-log-ingestion"` on CloudTrail/Flow Log resources
- Standard: `Project`, `Environment`, `Owner`, `ManagedBy`

Cloudcraft will auto-discover and render:
- Two NLBs (one per cluster)
- Two sets of EC2 instances in private subnets
- S3 buckets with data flow arrows
- CloudTrail trail → S3 bucket
- VPC Flow Log → S3 bucket
- Cloudflare tunnel (external)
