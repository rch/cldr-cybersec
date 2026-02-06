# System Architecture

## High-Level Architecture

```d2
direction: right

AWS: {
  label: "AWS Cloud"

  cloudtrail: {
    label: "CloudTrail"
    shape: cylinder
  }

  s3_raw: {
    label: "S3 Bucket\n(Raw Logs)"
    shape: cylinder
  }

  flink: {
    label: "Cloudera Data Flow\n(Flink)"
  }

  s3_tables: {
    label: "S3 Table Bucket\n(Iceberg)"
    shape: cylinder
  }

  glacier: {
    label: "S3 Glacier\n(7+ Year Archive)"
    shape: cylinder
  }

  clo: {
    label: "Cloudera Lakehouse\nOptimizer"
  }

  cloudtrail -> s3_raw: "Logs"
  s3_raw -> flink: "Ingest"
  flink -> s3_tables: "Iceberg\nwrites"
  s3_tables -> glacier: "Lifecycle\npolicy"
  clo -> s3_tables: "Optimize"
}

OnPrem: {
  label: "On-Prem Cloudera Cluster"

  flink_replication: {
    label: "Flink\nReplication"
  }

  iceberg_tables: {
    label: "Iceberg Tables\n(HDFS/Ozone)"
    shape: cylinder
  }

  spark_maint: {
    label: "Spark\nMaintenance"
  }

  impala: {
    label: "Impala / Hive\n(Deep Analysis)"
  }

  sdx: {
    label: "SDX\nGovernance"
  }

  flink_replication -> iceberg_tables: "Sync"
  iceberg_tables -> spark_maint: "Optimize"
  iceberg_tables -> impala: "Query"
  sdx -> iceberg_tables: "Govern"
}

AWS.s3_tables -> OnPrem.flink_replication: "Replicate\nevents"
```

## Design Principles

### Iceberg as Universal Format

Both AWS and on-prem environments use Apache Iceberg:

- **Schema evolution** without rewriting data
- **Time travel** for point-in-time queries
- **Partition evolution** to adapt to query patterns
- **Snapshot isolation** for consistent reads during writes

### S3 Table Buckets with CLO

S3 Tables provide native Iceberg with built-in catalog. We disable S3's automatic optimization and use Cloudera Lakehouse Optimizer (CLO) for consistent management across hybrid environment.

### Hybrid Query Strategy

| Query Type | Location | Tool | Use Case |
|------------|----------|------|----------|
| Direct query | AWS | S3 Tables | Lightweight verification |
| Ad-hoc investigation | On-prem | Impala | Security analysis |
| Historical deep dive | On-prem | Spark/Hive | Multi-year correlation |
| ML training | On-prem | Cloudera AI | Anomaly detection models |

## Subchapters

- [Data Flow](./data-flow.md): Event processing pipeline
- [Storage Strategy](./storage.md): Tiered storage architecture
