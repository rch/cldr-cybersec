# S3 Iceberg Tables

## Bucket Configuration

### Iceberg Data Bucket (OpenTofu)

```hcl
resource "aws_s3_bucket" "cloudtrail_iceberg" {
  bucket = "cybersec-cloudtrail-iceberg-${data.aws_caller_identity.current.account_id}"

  tags = {
    Environment = "production"
    Project     = "cybersec"
    Purpose     = "iceberg-tables"
  }
}

resource "aws_s3_bucket_versioning" "cloudtrail_iceberg" {
  bucket = aws_s3_bucket.cloudtrail_iceberg.id
  versioning_configuration {
    status = "Enabled"  # Required for Iceberg metadata consistency
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail_iceberg" {
  bucket = aws_s3_bucket.cloudtrail_iceberg.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.cybersec.arn
    }
    bucket_key_enabled = true
  }
}
```

## Disabling AWS Iceberg Optimizer

### Critical: Prevent Glue Optimizer Conflicts

AWS provides automatic Iceberg table optimization through Glue. This **must be disabled** to prevent conflicts with Cloudera Lakehouse Optimizer.

```hcl
# AWS Glue Data Catalog Database
resource "aws_glue_catalog_database" "cybersec" {
  name        = "cybersec"
  description = "Cybersec CloudTrail Iceberg tables"
}

# Register Iceberg table WITHOUT optimizer
resource "aws_glue_catalog_table" "cloudtrail_events" {
  database_name = aws_glue_catalog_database.cybersec.name
  name          = "cloudtrail_events"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "table_type"                     = "ICEBERG"
    "metadata_location"              = "s3://${aws_s3_bucket.cloudtrail_iceberg.id}/iceberg/warehouse/cloudtrail_events/metadata/00000-*.metadata.json"

    # CRITICAL: Disable AWS automatic optimization
    "optimization_enabled"           = "false"
    "compaction_enabled"             = "false"
    "snapshot_retention_enabled"     = "false"
    "orphan_file_deletion_enabled"   = "false"
  }

  # Iceberg manages schema internally, but Glue needs this for Athena
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.cloudtrail_iceberg.id}/iceberg/warehouse/cloudtrail_events"
    input_format  = "org.apache.iceberg.mr.hive.HiveIcebergInputFormat"
    output_format = "org.apache.iceberg.mr.hive.HiveIcebergOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.iceberg.mr.hive.HiveIcebergSerDe"
    }
  }
}
```

### Verify Optimizer is Disabled

After deployment, verify no automatic jobs are running:

```bash
# Check for Glue optimization jobs
aws glue get-table-optimizer \
  --catalog-id ${ACCOUNT_ID} \
  --database-name cybersec \
  --table-name cloudtrail_events \
  --type compaction

# Expected: ResourceNotFoundException (no optimizer configured)
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

## Query via Athena

### Verification Queries

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
