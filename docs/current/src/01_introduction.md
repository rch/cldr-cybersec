# Introduction

The Cybersec Toolkit provides a unified pipeline for ingesting, processing, and analyzing cybersecurity data—specifically AWS CloudTrail logs—across hybrid cloud environments.

## Goals

1. **Ingest CloudTrail logs** from AWS S3 using Apache Flink
2. **Land events in Iceberg format** on AWS S3 for lightweight query capability
3. **Replicate to on-prem Cloudera cluster** for deep analysis and long-term governance
4. **Archive to Glacier** for 7+ year retention with retrieval workflows
5. **Gradual migration** to on-prem as DR capabilities mature (1-3 year horizon)

## Key Principles

- **Iceberg everywhere**: Consistent table format across AWS and on-prem
- **Cloudera Lakehouse Optimizer (CLO)**: Use CLO for S3 Iceberg tables; disable AWS-native optimizer to avoid conflicts
- **Hybrid verification**: S3 Iceberg queries verify on-prem data completeness
- **Cold storage retrieval**: Automated workflows to restore Glacier data for analysis

## Technology Stack

| Component | AWS | On-Prem |
|-----------|-----|---------|
| Streaming | Flink (managed or self-hosted) | Cloudera Data Flow / Flink |
| Table Format | Iceberg | Iceberg |
| Storage | S3 + Glacier | HDFS / Ozone |
| Catalog | AWS Glue (limited) | Cloudera SDX |
| Optimizer | Cloudera Lakehouse Optimizer | Spark maintenance jobs* |
| Query | Athena (verification) | Impala / Hive |

*Note: Cloudera Lakehouse Optimizer is currently a cloud service. On-prem uses Spark-based
Iceberg table maintenance (compaction, snapshot expiration) via scheduled jobs.

## Document Structure

This roadmap covers:

- **Architecture**: High-level data flow and storage strategy
- **AWS Integration**: CloudTrail ingestion, S3 tables, Glacier archival
- **On-Prem Cluster**: Replication, Lakehouse Optimizer configuration
- **Operations**: Historical retrieval, data verification workflows
- **Roadmap**: Phased implementation timeline
