# AWS Integration

## Overview

```d2
direction: right

CloudTrail: {
  label: "AWS CloudTrail"
  shape: hexagon

  org_trail: "Organization Trail"
  mgmt_events: "Management Events"
  data_events: "Data Events"
  insights: "Insights Events"
}

S3: {
  label: "S3 Storage"

  raw_bucket: {
    label: "Raw Logs Bucket"
    shape: cylinder
  }
  table_bucket: {
    label: "S3 Table Bucket\n(Iceberg)"
    shape: cylinder
  }
}

Flink: {
  label: "Cloudera Data Flow"

  cluster: "Flink Cluster"
  job: "CloudTrail\nProcessor Job"
}

CLO: {
  label: "Cloudera Lakehouse\nOptimizer"

  compaction: "Compaction"
  snapshots: "Snapshot\nManagement"
}

CloudTrail -> S3.raw_bucket: "Deliver logs"
S3.raw_bucket -> Flink.job: "Ingest"
Flink.job -> S3.table_bucket: "Write Iceberg"
CLO -> S3.table_bucket: "Optimize"
```

## Key Design Decisions

### 1. S3 Table Buckets

S3 Tables provide native Iceberg support with built-in catalog for direct queries. We disable S3's automatic optimization and use CLO instead for consistent management across hybrid environment.

### 2. Cloudera Lakehouse Optimizer (CLO)

CLO manages S3 table buckets:

- Compaction and file optimization
- Snapshot expiration
- Consistent optimization strategy with on-prem

### 3. Flink for Ingestion

Flink provides:

- Low latency (seconds)
- Exactly-once semantics
- Consistent with on-prem Cloudera Data Flow (CDF)
- Fine-grained checkpointing and state management

## OpenTofu Configuration

See subsections for detailed infrastructure-as-code:

- [CloudTrail Ingestion](./03_01_cloudtrail-ingestion.md)
- [S3 Iceberg Tables](./03_02_s3-iceberg-tables.md)
- [Glacier Archival](./03_03_glacier-archival.md)
