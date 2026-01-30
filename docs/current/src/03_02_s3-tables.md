# S3 Table Buckets

## Overview

S3 Tables provide native Iceberg support with a built-in catalog for direct queries. We use S3 table buckets for CloudTrail events, with Cloudera Lakehouse Optimizer (CLO) managing table optimization.

## Table Bucket Configuration (OpenTofu)

```hcl
resource "aws_s3tables_table_bucket" "cloudtrail" {
  name = "cybersec-cloudtrail-${data.aws_caller_identity.current.account_id}"

  # Disable S3's built-in maintenance - CLO handles this
  maintenance_configuration {
    iceberg_compaction {
      settings {
        target_file_size_mb = 512
      }
      status = "disabled"
    }
    iceberg_snapshot_management {
      settings {
        max_snapshot_age_hours = 168
        min_snapshots_to_keep  = 100
      }
      status = "disabled"
    }
  }
}

resource "aws_s3tables_namespace" "cybersec" {
  table_bucket_arn = aws_s3tables_table_bucket.cloudtrail.arn
  namespace        = ["cybersec"]
}
```

## CLO Configuration

Cloudera Lakehouse Optimizer manages the S3 table bucket:

```yaml
# clo-config.yaml
catalogs:
  - name: aws-cybersec
    type: s3-tables
    table_bucket_arn: arn:aws:s3tables:us-east-1:123456789012:bucket/cybersec-cloudtrail

optimization:
  tables:
    - catalog: aws-cybersec
      namespace: cybersec
      table: cloudtrail_events
      compaction:
        enabled: true
        target_file_size_mb: 512
      snapshot_expiration:
        enabled: true
        retain_last: 100
        max_age_hours: 168
```

## Table Schema

### CloudTrail Events Table

```sql
CREATE TABLE cybersec.cloudtrail_events (
  -- Event identification
  event_id STRING,
  event_time TIMESTAMP,
  event_source STRING,
  event_name STRING,
  event_type STRING,

  -- AWS context
  aws_region STRING,
  source_ip_address STRING,
  user_agent STRING,

  -- Identity
  user_identity STRUCT<
    type: STRING,
    principal_id: STRING,
    arn: STRING,
    account_id: STRING,
    user_name: STRING,
    session_context: STRUCT<
      session_issuer: STRUCT<
        type: STRING,
        principal_id: STRING,
        arn: STRING,
        account_id: STRING,
        user_name: STRING
      >,
      attributes: STRUCT<
        mfa_authenticated: STRING,
        creation_date: TIMESTAMP
      >
    >
  >,

  -- Request/Response
  request_parameters STRING,  -- JSON string
  response_elements STRING,   -- JSON string
  error_code STRING,
  error_message STRING,

  -- Resources
  resources ARRAY<STRUCT<
    arn: STRING,
    account_id: STRING,
    type: STRING
  >>,

  -- Enrichment (added by Flink)
  geo_country STRING,
  geo_city STRING,
  asn_org STRING,

  -- Metadata
  ingested_at TIMESTAMP,
  raw_event STRING  -- Original JSON for forensics
)
USING iceberg
PARTITIONED BY (
  year(event_time),
  month(event_time),
  day(event_time),
  aws_region
)
LOCATION 's3://cybersec-cloudtrail-iceberg-{account}/iceberg/warehouse/cloudtrail_events'
TBLPROPERTIES (
  'format-version' = '2',
  'write.format.default' = 'parquet',
  'write.parquet.compression-codec' = 'zstd',
  'write.metadata.compression-codec' = 'gzip'
);
```

## Direct Query via S3 Tables

S3 Tables' built-in catalog enables direct queries without external catalog setup:

```sql
-- Count events by day (verify ingestion)
SELECT
  date_trunc('day', event_time) as event_day,
  count(*) as event_count
FROM cybersec.cloudtrail_events
WHERE event_time >= current_date - interval '7' day
GROUP BY 1
ORDER BY 1 DESC;

-- Compare with on-prem counts
SELECT
  date_trunc('hour', event_time) as event_hour,
  count(*) as aws_count
FROM cybersec.cloudtrail_events
WHERE event_time >= current_timestamp - interval '24' hour
GROUP BY 1
ORDER BY 1;
```
