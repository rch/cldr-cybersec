# Flink Iceberg Integration for CloudTrail Events

This module provides native Apache Flink integration with Apache Iceberg for writing AWS CloudTrail events to MinIO using a PostgreSQL catalog.

## Architecture

```
Flink DataGen → Flink Table API → Iceberg Connector → MinIO (S3)
                                         ↓
                                 PostgreSQL Catalog
```

## Components

### CloudTrailIcebergWriter.java
Utility class providing methods to:
- Configure Iceberg catalog with PostgreSQL backend
- Create CloudTrail events table schema
- Write DataStream to Iceberg tables
- Handle S3/MinIO configuration

### CloudTrailDataGenIcebergJob.java
Standalone Flink job that:
- Generates synthetic CloudTrail events using Flink DataGen connector
- Writes events directly to Iceberg tables in MinIO
- Uses PostgreSQL for Iceberg metadata catalog

## Prerequisites

1. **Infrastructure running** (via `devenv up`):
   - PostgreSQL on port 5438
   - MinIO on ports 9010 (API) / 9011 (Console)
   - Flink on port 8081

2. **Maven dependencies** (already added to pom.xml):
   - `iceberg-flink-runtime-1.18` - Flink-Iceberg connector
   - `iceberg-core` - Iceberg core library
   - `iceberg-aws` - AWS S3 integration
   - `software.amazon.awssdk:s3` - AWS SDK for S3/MinIO

## Building

From the `flink-cyber` directory:

```bash
mvn clean package -DskipTests
```

The JAR will be located at:
```
flink-cyber/flink-common/target/flink-common-2.4.0.jar
```

## Running the Job

### Using Flink CLI

```bash
# Submit job to Flink cluster
flink run \
  -c com.cloudera.cyber.flink.iceberg.CloudTrailDataGenIcebergJob \
  flink-common/target/flink-common-2.4.0.jar \
  --postgres.host localhost \
  --postgres.port 5438 \
  --postgres.db iceberg \
  --postgres.user $USER \
  --minio.endpoint http://localhost:9010 \
  --minio.access-key minioadmin \
  --minio.secret-key minioadmin \
  --rows-per-second 10
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `postgres.host` | localhost | PostgreSQL hostname |
| `postgres.port` | 5438 | PostgreSQL port |
| `postgres.db` | iceberg | PostgreSQL database name |
| `postgres.user` | $USER | PostgreSQL username |
| `minio.endpoint` | http://localhost:9010 | MinIO API endpoint |
| `minio.access-key` | minioadmin | MinIO access key |
| `minio.secret-key` | minioadmin | MinIO secret key |
| `rows-per-second` | 10 | DataGen events per second |

## Table Schema

The CloudTrail events table (`cybersec.cloudtrail_events`) has the following schema:

```sql
CREATE TABLE iceberg_catalog.cybersec.cloudtrail_events (
  event_version STRING,
  event_id STRING,
  event_time TIMESTAMP(3),
  event_name STRING,
  aws_region STRING,
  source_ip_address STRING,
  user_agent STRING,
  event_source STRING,
  user_identity_type STRING,
  user_identity_arn STRING,
  user_identity_account_id STRING,
  request_parameters STRING,
  response_elements STRING,
  PRIMARY KEY (event_id) NOT ENFORCED
)
```

## Verifying Data

### 1. Check Flink Web UI
Visit http://localhost:8081 to see running jobs and metrics.

### 2. Check MinIO Console
Visit http://localhost:9011 (credentials: minioadmin/minioadmin) and browse to:
```
cybersec/iceberg/warehouse/cybersec/cloudtrail_events/
```

You should see:
- `metadata/` - Table metadata and manifests
- `data/` - Parquet data files

### 3. Query via PostgreSQL Catalog

```bash
psql -h localhost -p 5438 -U $USER iceberg -c "
  SELECT * FROM iceberg_catalog_tables 
  WHERE table_name = 'cloudtrail_events';
"
```

### 4. Query via PyIceberg (Python)

```python
from pyiceberg.catalog.sql import SqlCatalog

catalog = SqlCatalog(
    "iceberg_catalog",
    uri="postgresql+psycopg2://user@localhost:5438/iceberg",
    warehouse="s3://cybersec/iceberg/warehouse",
    s3__endpoint="http://localhost:9010",
    s3__access_key_id="minioadmin",
    s3__secret_access_key="minioadmin"
)

table = catalog.load_table("cybersec.cloudtrail_events")
df = table.scan().to_pandas()
print(f"Total events: {len(df)}")
print(df.head())
```

## Advantages over PyIceberg Approach

1. **Native Integration**: Uses Flink's Iceberg connector directly
2. **Better Performance**: No intermediate JSON files needed
3. **Exactly-Once Semantics**: Flink's checkpoint mechanism ensures data consistency
4. **Streaming**: True streaming ingestion without batch processing
5. **Metadata Management**: Iceberg metadata is properly maintained
6. **Schema Evolution**: Built-in support for schema changes
7. **Partition Management**: Automatic partition handling

## Configuration Details

### Iceberg Catalog Configuration

The job creates an Iceberg catalog with these properties:

```properties
type = iceberg
catalog-type = jdbc
uri = jdbc:postgresql://localhost:5438/iceberg
warehouse = s3a://cybersec/iceberg/warehouse
io-impl = org.apache.iceberg.aws.s3.S3FileIO
s3.endpoint = http://localhost:9010
s3.path-style-access = true
s3.access-key-id = minioadmin
s3.secret-access-key = minioadmin
```

### Table Properties

```properties
format-version = 2
write.format.default = parquet
write.parquet.compression-codec = snappy
```

## Troubleshooting

### ClassNotFoundException: org.apache.iceberg.*
Ensure the Iceberg runtime JAR is in the Flink lib directory or use `-C` flag:
```bash
flink run -C file:///path/to/iceberg-flink-runtime-1.18-1.4.3.jar ...
```

### S3 Connection Refused
Check MinIO is running:
```bash
curl http://localhost:9010/minio/health/live
```

### PostgreSQL Connection Failed
Verify PostgreSQL is accessible:
```bash
psql -h localhost -p 5438 -U $USER iceberg -c "SELECT 1"
```

### No Data in Iceberg Table
- Check Flink job is running in Web UI
- Review Flink TaskManager logs for errors
- Verify catalog and table creation succeeded

## Next Steps

1. **Add Real CloudTrail Parsing**: Replace DataGen with actual CloudTrail JSON parsing
2. **Add Partitioning**: Partition by date for better query performance
3. **Add Compaction**: Implement periodic compaction for small files
4. **Add Schema Evolution**: Handle CloudTrail schema changes
5. **Add Monitoring**: Export metrics to Prometheus/Grafana
6. **Add Data Quality**: Implement validation and deduplication

## References

- [Apache Iceberg Flink Documentation](https://iceberg.apache.org/docs/latest/flink/)
- [Flink DataGen Connector](https://nightlies.apache.org/flink/flink-docs-master/docs/connectors/table/datagen/)
- [MinIO S3 Compatibility](https://min.io/docs/minio/linux/integrations/aws-cli-with-minio.html)
