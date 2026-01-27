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
  iceberg_bucket: {
    label: "Iceberg Tables Bucket"
    shape: cylinder
  }
}

Catalog: {
  label: "Iceberg REST Catalog"

  rest_api: "REST API"
  optimizer: "AWS Optimizer\n(DISABLED)"
}

Flink: {
  label: "Apache Flink"

  cluster: "Flink Cluster\n(EKS or EMR)"
  job: "CloudTrail\nProcessor Job"
}

Query: {
  label: "Query Layer"

  athena: "Athena\n(verification)"
}

CloudTrail -> S3.raw_bucket: "Deliver logs"
S3.raw_bucket -> Flink.job: "Ingest"
Flink.job -> S3.iceberg_bucket: "Write Iceberg"
S3.iceberg_bucket -> Catalog.rest_api: "Register tables"
Catalog.rest_api -> Query.athena: "Metadata"
S3.iceberg_bucket -> Query.athena: "Scan data"

Catalog.optimizer -> S3.iceberg_bucket: "DISABLED" {
  style.stroke: "#ff0000"
  style.stroke-dash: 5
}
```

## Key Design Decisions

### 1. Disable AWS Iceberg Optimizer

AWS provides automatic Iceberg optimization (compaction, snapshot expiration). We disable this to:

- Avoid conflicts with Cloudera Lakehouse Optimizer
- Maintain consistent optimization strategy across hybrid environment
- Preserve snapshots for replication verification

### 2. Iceberg REST Catalog

We use Cloudera's Iceberg REST Catalog for:

- Table metadata registration (schema, partitioning)
- Consistent catalog interface across AWS and on-prem
- Athena query access via catalog integration

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
