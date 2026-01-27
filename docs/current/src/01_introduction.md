# Introduction

The Cybersec Toolkit provides a unified pipeline for ingesting, processing, and analyzing cybersecurity data—specifically AWS CloudTrail logs—across hybrid cloud environments.

## Goals

1. **Ingest CloudTrail logs** from AWS S3 using Flink
2. **Land events in S3 Table Buckets** (native Iceberg) for lightweight query capability
3. **Replicate to on-prem Cloudera cluster** for deep analysis and long-term governance
4. **Archive to Glacier** for 7+ year retention with retrieval workflows
5. **Gradual migration** to on-prem as DR capabilities mature (1-3 year horizon)

## Key Principles

- **Iceberg everywhere**: Consistent table format across AWS and on-prem
- **Cloudera Lakehouse Optimizer (CLO)**: Use CLO for S3 table buckets; disable S3's built-in optimizer
- **Hybrid verification**: S3 Tables direct queries verify on-prem data completeness
- **Cold storage retrieval**: Automated workflows to restore Glacier data for analysis

## Technology Stack

| Component | AWS | On-Prem |
|-----------|-----|---------|
| Streaming | Cloudera Data Flow (Flink) | Cloudera Data Flow (Flink) |
| Table Format | Iceberg (S3 Tables) | Iceberg |
| Storage | S3 Table Buckets + Glacier | HDFS / Ozone |
| Catalog | S3 Tables (built-in) | Iceberg REST Catalog |
| Optimizer | Cloudera Lakehouse Optimizer | Spark maintenance jobs* |
| Query | S3 Tables (direct) | Impala / Hive |

*Note: Cloudera Lakehouse Optimizer is currently a cloud service. On-prem uses Spark-based
Iceberg table maintenance (compaction, snapshot expiration) via scheduled jobs.

## Document Structure

This roadmap covers:

- **Architecture**: High-level data flow and storage strategy
- **AWS Integration**: CloudTrail ingestion, S3 tables, Glacier archival
- **On-Prem Cluster**: Replication, Lakehouse Optimizer configuration
- **Operations**: Historical retrieval, data verification workflows
- **Roadmap**: Phased implementation timeline
