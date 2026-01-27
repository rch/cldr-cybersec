# Architecture Overview

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
    label: "Apache Flink\n(Ingestion)"
  }

  s3_iceberg: {
    label: "S3 Iceberg Tables\n(Processed Events)"
    shape: cylinder
  }

  glacier: {
    label: "S3 Glacier\n(7+ Year Archive)"
    shape: cylinder
  }

  athena: {
    label: "Athena\n(Verification Queries)"
  }

  cloudtrail -> s3_raw: "Logs"
  s3_raw -> flink: "Ingest"
  flink -> s3_iceberg: "Iceberg\nwrites"
  s3_iceberg -> glacier: "Lifecycle\npolicy"
  s3_iceberg -> athena: "Query"
}

OnPrem: {
  label: "On-Prem Cloudera Cluster"

  replication: {
    label: "Replication\nManager"
  }

  iceberg_tables: {
    label: "Iceberg Tables\n(HDFS/Ozone)"
    shape: cylinder
  }

  lakehouse_opt: {
    label: "Lakehouse\nOptimizer"
  }

  impala: {
    label: "Impala / Hive\n(Deep Analysis)"
  }

  sdx: {
    label: "SDX\nGovernance"
  }

  replication -> iceberg_tables: "Sync"
  iceberg_tables -> lakehouse_opt: "Optimize"
  iceberg_tables -> impala: "Query"
  sdx -> iceberg_tables: "Govern"
}

AWS.s3_iceberg -> OnPrem.replication: "Replicate\nevents"
AWS.athena -> OnPrem.impala: "Verify\ncompleteness" {style.stroke-dash: 5}
```

## Design Principles

### Iceberg as Universal Format

Both AWS and on-prem environments use Apache Iceberg:

- **Schema evolution** without rewriting data
- **Time travel** for point-in-time queries
- **Partition evolution** to adapt to query patterns
- **Snapshot isolation** for consistent reads during writes

### Optimizer Conflict Resolution

AWS provides native Iceberg optimization, but this conflicts with Cloudera's Lakehouse Optimizer. Our approach:

1. **Disable AWS optimizer** via OpenTofu configuration
2. **Use Cloudera Lakehouse Optimizer** exclusively for compaction and optimization
3. **S3 tables remain queryable** via Athena for verification purposes

### Hybrid Query Strategy

| Query Type | Location | Tool | Use Case |
|------------|----------|------|----------|
| Verification | AWS | Athena | Confirm all events replicated |
| Ad-hoc investigation | On-prem | Impala | Security analysis |
| Historical deep dive | On-prem | Spark/Hive | Multi-year correlation |
| ML training | On-prem | Cloudera AI | Anomaly detection models |
