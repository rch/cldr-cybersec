# CloudTrail Event Processing Configuration

## Kafka Topics

### cloudtrail-raw
Raw CloudTrail events from the data generator
- Format: JSON
- Rate: 10 events/second (configurable)
- Consumer: cloudtrail_processor.py

### cloudtrail-parsed
Parsed and structured CloudTrail events
- Format: JSON
- Schema: See below
- Consumer: cloudtrail_writer.py (Iceberg)

## Event Schema

### Raw CloudTrail Event (cloudtrail-raw)
```json
{
  "eventVersion": "1.08",
  "userIdentity": {
    "type": "IAMUser",
    "principalId": "AIDAI23EXAMPLE",
    "arn": "arn:aws:iam::123456789012:user/testuser1",
    "accountId": "123456789012",
    "accessKeyId": "AKIAIOSFODNN7EXAMPLE"
  },
  "eventTime": "2026-01-20T10:30:45Z",
  "eventSource": "s3.amazonaws.com",
  "eventName": "GetObject",
  "awsRegion": "us-east-1",
  "sourceIPAddress": "192.168.1.100",
  "userAgent": "aws-cli/2.13.0",
  "requestParameters": {
    "bucketName": "test-bucket-123",
    "key": "data/file-456.json"
  },
  "responseElements": {
    "requestId": "abc123def456"
  },
  "requestID": "abc123def456",
  "eventID": "12345678-1234-1234-1234-123456789012",
  "readOnly": true,
  "eventType": "AwsApiCall",
  "managementEvent": true,
  "recipientAccountId": "123456789012",
  "eventCategory": "Management"
}
```

### Parsed CloudTrail Event (cloudtrail-parsed)
```json
{
  "event_version": "1.08",
  "event_timestamp": "2026-01-20T10:30:45Z",
  "event_source": "s3.amazonaws.com",
  "event_name": "GetObject",
  "aws_region": "us-east-1",
  "source_ip": "192.168.1.100",
  "user_agent": "aws-cli/2.13.0",
  "user_type": "IAMUser",
  "user_arn": "arn:aws:iam::123456789012:user/testuser1",
  "account_id": "123456789012",
  "event_id": "12345678-1234-1234-1234-123456789012",
  "read_only": true,
  "event_type": "AwsApiCall",
  "processing_time": "2026-01-20T10:30:46.123456Z"
}
```

## Iceberg Table Configuration

### Catalog
- Type: SQL (PostgreSQL) - uses `pyiceberg[sql-postgres]` extra
- Database: cybersec
- Schema: iceberg
- Connection: `postgresql://postgres@localhost:5438/cybersec`

### Warehouse
- Location: `s3://cybersec/iceberg/warehouse`
- Storage: MinIO (localhost:9010) - uses `pyiceberg[s3fs]` extra
- Format: Parquet
- File I/O: FsspecFileIO (s3fs backend)

### Table: cybersec.cloudtrail_events

#### Partitioning
- Partition by: `event_date` (day transform on `event_timestamp`)
- Benefits:
  - Efficient time-range queries
  - Partition pruning
  - Easy data lifecycle management

#### Sorting
Primary sort order:
1. `event_timestamp` (ascending)
2. `event_id` (ascending)

Benefits:
- Faster time-based queries
- Better compression
- Efficient range scans

#### Sample Partition Layout
```
s3://cybersec/iceberg/warehouse/
  cybersec/
    cloudtrail_events/
      metadata/
        v1.metadata.json
        snap-123456789.avro
      data/
        event_date=2026-01-20/
          00000-0-abc123.parquet
          00001-0-def456.parquet
        event_date=2026-01-21/
          00000-0-ghi789.parquet
```

## Flink Configuration

### DataGen Job
```yaml
rows-per-second: 10
fields:
  event_id:
    kind: sequence
    start: 1
    end: 1000000
```

### Processor Job
```yaml
parallelism: 2
checkpoint-interval: 60000  # 60 seconds
state-backend: filesystem
```

### Resources
- TaskManager slots: 4
- Parallelism: 2
- Checkpoint interval: 60s

## Environment Variables Reference

### Required
```bash
# Iceberg Catalog
export ICEBERG_CATALOG_URI="postgresql://postgres@localhost:5438/cybersec"
export ICEBERG_WAREHOUSE="s3://cybersec/iceberg/warehouse"

# MinIO/S3
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin"
export S3_ENDPOINT="http://localhost:9010"

# Kafka
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
```

### Optional
```bash
# Flink
export FLINK_HOME="/opt/flink"
export FLINK_STATE_DIR="$HOME/.devenv/state/flink"

# Logging
export LOG_LEVEL="INFO"
export PYTHONUNBUFFERED="1"
```

## Query Examples

### Time-based Queries
```python
# Last 24 hours
events = query.query_recent_events(hours=24)

# Last hour with limit
events = query.query_recent_events(hours=1, limit=1000)
```

### Event-specific Queries
```python
# All console logins
logins = query.query_by_event_name("ConsoleLogin")

# S3 operations
s3_ops = query.query_by_event_name("GetObject")
```

### IP-based Queries
```python
# Events from specific IP
events = query.query_by_source_ip("192.168.1.100")
```

### Statistics
```python
stats = query.get_event_statistics(hours=24)
# Returns:
# {
#   "total_events": 86400,
#   "unique_event_names": 16,
#   "unique_accounts": 50,
#   "unique_ips": 234,
#   "unique_regions": 4,
#   "read_only_events": 45000
# }
```

## Monitoring Queries

### PostgreSQL Catalog Queries
```sql
-- List all tables
SELECT * FROM iceberg.catalog_tables;

-- Table metadata
SELECT 
  table_name,
  metadata_location,
  previous_metadata_location
FROM iceberg.catalog_tables
WHERE catalog_name = 'cybersec';
```

### Performance Metrics
```python
import pyarrow.parquet as pq

# Read Parquet file stats
parquet_file = pq.ParquetFile('path/to/file.parquet')
print(parquet_file.metadata)
print(parquet_file.schema)

# Row group statistics
for i in range(parquet_file.num_row_groups):
    rg = parquet_file.metadata.row_group(i)
    print(f"Row group {i}: {rg.num_rows} rows")
```

## Data Retention

### Iceberg Features for Lifecycle Management

```python
# Expire old snapshots
table.expire_snapshots(
    older_than=datetime.now() - timedelta(days=30)
)

# Remove orphan files
table.remove_orphans()

# Rewrite manifest files
table.rewrite_manifests()
```

### PostgreSQL Cleanup (using pg_cron)
```sql
-- Schedule weekly cleanup
SELECT cron.schedule(
  'iceberg-cleanup',
  '0 2 * * 0',  -- Every Sunday at 2 AM
  $$
    DELETE FROM iceberg.catalog_tables 
    WHERE metadata_location IS NULL 
      AND updated_at < NOW() - INTERVAL '90 days'
  $$
);
```

## Scaling Considerations

### Horizontal Scaling
- Increase Flink TaskManager slots
- Add more Kafka partitions
- Run multiple Iceberg writers

### Vertical Scaling
- Increase TaskManager memory
- Larger batch sizes for Iceberg writes
- More CPU cores for Flink

### Storage Scaling
- MinIO can be clustered for HA
- PostgreSQL can use replication
- Iceberg supports multiple table formats
