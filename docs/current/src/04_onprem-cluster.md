# On-Prem Cloudera Cluster

## Architecture

```d2
direction: right

AWS: {
  label: "AWS"

  s3_iceberg: {
    label: "S3 Iceberg Tables"
    shape: cylinder
  }
}

Network: {
  label: "Network"

  vpn: "VPN / Direct Connect"
}

OnPrem: {
  label: "Cloudera Data Platform (Private Cloud)"

  Ingest: {
    label: "Ingestion Layer"

    repl_mgr: "Replication\nManager"
    nifi: "NiFi\n(edge ingest)"
    kafka: "Kafka\n(event bus)"
  }

  Compute: {
    label: "Compute Layer"

    flink: "Flink\n(streaming)"
    spark: "Spark\n(batch)"
    impala: "Impala\n(SQL queries)"
  }

  Storage: {
    label: "Storage Layer"

    ozone: {
      label: "Ozone\n(object store)"
      shape: cylinder
    }
    hdfs: {
      label: "HDFS\n(legacy)"
      shape: cylinder
    }
  }

  Services: {
    label: "Platform Services"

    sdx: "SDX\n(security & governance)"
    lakehouse_opt: "Lakehouse\nOptimizer"
    ranger: "Ranger\n(access control)"
    atlas: "Atlas\n(lineage)"
  }

  Analytics: {
    label: "Analytics"

    cdv: "Cloudera Data\nVisualization"
    cai: "Cloudera AI\n(ML Workbench)"
  }
}

AWS.s3_iceberg -> Network.vpn
Network.vpn -> OnPrem.Ingest.repl_mgr

OnPrem.Ingest.repl_mgr -> OnPrem.Storage.ozone: "Replicate tables"
OnPrem.Ingest.kafka -> OnPrem.Compute.flink
OnPrem.Compute.flink -> OnPrem.Storage.ozone
OnPrem.Compute.spark -> OnPrem.Storage.ozone
OnPrem.Compute.impala -> OnPrem.Storage.ozone

OnPrem.Storage.ozone -> OnPrem.Services.lakehouse_opt: "Optimize"
OnPrem.Services.sdx -> OnPrem.Storage.ozone: "Govern"
OnPrem.Services.sdx -> OnPrem.Storage.hdfs: "Govern"

OnPrem.Compute.impala -> OnPrem.Analytics.cdv
OnPrem.Storage.ozone -> OnPrem.Analytics.cai
```

## Cluster Sizing

### Initial Deployment

| Role | Nodes | Specs | Purpose |
|------|-------|-------|---------|
| Master | 3 | 32 vCPU, 128GB RAM | HDFS NN, YARN RM, Hive Metastore |
| Worker | 6 | 64 vCPU, 256GB RAM, 24TB NVMe | HDFS DN, YARN NM, Impala |
| Edge | 2 | 16 vCPU, 64GB RAM | NiFi, Flink, Gateway |
| Ozone | 3 | 32 vCPU, 128GB RAM, 100TB HDD | SCM, OM, DataNodes |

### Growth Path

| Phase | Timeline | Workers | Storage | Workload |
|-------|----------|---------|---------|----------|
| Initial | Month 0 | 6 | 144TB | 90-day data |
| Phase 2 | Month 6 | 12 | 288TB | 1-year data |
| Phase 3 | Year 2 | 24 | 576TB | 3-year data |
| Full DR | Year 3 | 48 | 1.2PB | 7-year data |

## Data Layout

### Iceberg Table Location

```
ozone://cybersec/iceberg/warehouse/
├── cloudtrail_events/
│   ├── metadata/
│   │   ├── v1.metadata.json
│   │   ├── v2.metadata.json
│   │   └── ...
│   └── data/
│       ├── year=2026/
│       │   ├── month=01/
│       │   │   ├── day=27/
│       │   │   │   ├── region=us-east-1/
│       │   │   │   │   └── 00000-0-*.parquet
│       │   │   │   └── region=us-west-2/
│       │   │   │       └── 00000-0-*.parquet
│       │   │   └── ...
│       │   └── ...
│       └── ...
└── enrichment_tables/
    ├── geoip/
    └── asn/
```

## Security Configuration

### SDX Integration

All data access governed by Ranger policies synchronized via SDX:

```yaml
# Example Ranger policy for CloudTrail data
policy:
  name: "cloudtrail-security-team"
  resource:
    database: "cybersec"
    table: "cloudtrail_events"
    column: "*"
  permissions:
    - access_types: ["select"]
      users: []
      groups: ["security-analysts"]
      conditions:
        - type: "row-filter"
          expression: "aws_region IN ('us-east-1', 'us-west-2')"
```

### Encryption

| Layer | Method |
|-------|--------|
| In-transit | TLS 1.3 |
| At-rest (Ozone) | Transparent Data Encryption (TDE) |
| At-rest (HDFS) | HDFS Encryption Zones |
| Key Management | Cloudera Navigator Key Trustee |
