# Flink DataGen to Iceberg E2E Test

## Overview

This E2E test demonstrates the complete data pipeline:
- **Flink DataGen** → Generates synthetic test data
- **Apache Iceberg** → ACID table format with versioning
- **Apache Polaris** → REST catalog for Iceberg metadata
- **MinIO** → S3-compatible object storage for data files
- **PostgreSQL** → Polaris metadata storage

## Quick Start

### Prerequisites

Ensure all services are running:
```bash
# Check Flink
curl http://localhost:8081

# Check Polaris (will return 401 - that's expected)
curl http://localhost:8181

# Check MinIO
curl http://localhost:9010/minio/health/live

# Check PostgreSQL
psql postgresql://postgres@localhost:5438/cybersec -c "SELECT 1"
```

### Run the E2E Test

```bash
./test_flink_iceberg_e2e.sh
```

This script will:
1. ✓ Verify all prerequisites
2. ✓ Clean up previous test data
3. ✓ Create Iceberg catalog and table
4. ✓ Insert 100 test records (batch mode)
5. ✓ Verify data in Iceberg
6. ✓ Check files in MinIO
7. ✓ Show table metadata

## Test SQL Script

The test uses `test_flink_iceberg_e2e.sql` which creates:

**DataGen Source Table:**
- `id`: Random 10-character string
- `name`: Random 20-character string  
- `amount`: Random number 1-1000
- `region`: Random 10-character string (partition key)
- `created_at`: Random timestamp as BIGINT

**Iceberg Target Table:**
- Same schema as source
- Partitioned by `region`
- Primary key on `id` (not enforced)
- Stored in MinIO at `s3://cybersec/iceberg/warehouse/test_db/test_events/`

## Key Configuration

### Polaris Catalog Setup

The catalog **must** be created with storage configuration:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "catalog": {
      "name": "cybersec",
      "type": "INTERNAL",
      "properties": {
        "default-base-location": "s3://cybersec/iceberg/warehouse"
      },
      "storageConfigInfo": {
        "storageType": "S3",
        "endpoint": "http://localhost:9010",
        "pathStyleAccess": true,
        "allowedLocations": ["s3://cybersec"]
      }
    }
  }' \
  http://localhost:8181/api/management/v1/catalogs
```

**Critical**: The `pathStyleAccess: true` setting at the catalog level is required for MinIO compatibility.

### Flink Hadoop Configuration

Located in `${FLINK_HOME}/conf/core-site.xml`:

```xml
<configuration>
    <property>
        <name>fs.s3a.endpoint</name>
        <value>http://localhost:9010</value>
    </property>
    <property>
        <name>fs.s3a.path.style.access</name>
        <value>true</value>
    </property>
    <property>
        <name>fs.s3a.access.key</name>
        <value>minioadmin</value>
    </property>
    <property>
        <name>fs.s3a.secret.key</name>
        <value>minioadmin</value>
    </property>
    <property>
        <name>fs.s3a.connection.ssl.enabled</name>
        <value>false</value>
    </property>
</configuration>
```

## Verifying Results

### 1. Query via Flink SQL

```bash
${FLINK_HOME}/bin/sql-client.sh
```

```sql
CREATE CATALOG iceberg_catalog WITH (
  'type' = 'iceberg',
  'catalog-type' = 'rest',
  'uri' = 'http://localhost:8181/api/catalog',
  'warehouse' = 'cybersec',
  'credential' = 'admin:admin',
  'oauth2-server-uri' = 'http://localhost:8181/api/catalog/v1/oauth/tokens',
  'scope' = 'PRINCIPAL_ROLE:ALL'
);

USE CATALOG iceberg_catalog;
USE test_db;

-- Show tables
SHOW TABLES;

-- Describe table
DESCRIBE test_events;

-- Query data (when available)
SELECT COUNT(*) FROM test_events;
SELECT * FROM test_events LIMIT 10;
```

### 2. Check Files in MinIO

Using MinIO Client:
```bash
# Set up alias
mc alias set local http://localhost:9010 minioadmin minioadmin

# List all files
mc ls --recursive local/cybersec/iceberg/warehouse/test_db/test_events/

# Check metadata
mc ls local/cybersec/iceberg/warehouse/test_db/test_events/metadata/

# Check data files  
mc ls local/cybersec/iceberg/warehouse/test_db/test_events/data/
```

Using MinIO Web UI:
- Open http://localhost:9010
- Login: minioadmin / minioadmin
- Navigate to: cybersec → iceberg → warehouse → test_db → test_events

### 3. Query via Python Iceberg Browser

```bash
python iceberg_browser.py
```

Then query the table through the PyIceberg interface.

## Streaming vs Batch Inserts

### Batch Insert (Default in test)

```sql
INSERT INTO test_events
SELECT * FROM default_catalog.default_database.test_datagen
LIMIT 100;
```

This inserts exactly 100 records and completes.

### Streaming Insert

```sql
-- Run continuously until stopped
INSERT INTO test_events
SELECT * FROM default_catalog.default_database.test_datagen;
```

This runs indefinitely, inserting ~5 records/second. Stop with Ctrl+C or via Flink UI.

## Troubleshooting

### UnknownHostException Error

If you see `UnknownHostException when attempting to interact with a service`:

1. **Check Polaris catalog storage config**: The catalog must have `pathStyleAccess: true` in `storageConfigInfo`
2. **Verify core-site.xml**: Must have `fs.s3a.path.style.access = true`
3. **Restart Flink**: After config changes, restart the cluster

### Table Creation Succeeds but INSERT Fails

- Check Flink logs: `${FLINK_HOME}/log/flink-*-sql-client-*.log`
- Verify MinIO is accessible: `curl http://localhost:9010/minio/health/live`
- Check S3 credentials in core-site.xml

### "TIMESTAMP(3) unsupported" Error

Iceberg in Flink has limited TIMESTAMP support. Use BIGINT for epoch milliseconds instead:

```sql
-- ❌ Don't use
event_time TIMESTAMP(3)

-- ✅ Use instead
event_time BIGINT  -- Epoch milliseconds
```

## Architecture

```
┌─────────────┐
│   Flink     │
│  DataGen    │
└──────┬──────┘
       │
       │ Generates test data
       ▼
┌─────────────────────────┐
│   Flink SQL Runtime     │
│                         │
│  ┌───────────────────┐  │
│  │ Iceberg Connector │  │
│  └────────┬──────────┘  │
└───────────┼─────────────┘
            │
            │ REST API
            ▼
     ┌─────────────┐
     │   Polaris   │──────► PostgreSQL
     │ REST Catalog│        (metadata)
     └──────┬──────┘
            │
            │ Storage config with
            │ pathStyleAccess:true
            ▼
      ┌──────────┐
      │  MinIO   │
      │   S3     │
      └──────────┘
      Data files:
      - metadata/*.json
      - data/*.parquet
```

## Files

- `test_flink_iceberg_e2e.sh` - Main test script
- `test_flink_iceberg_e2e.sql` - Flink SQL test definition
- `${FLINK_HOME}/conf/core-site.xml` - Hadoop S3A configuration
- `${FLINK_HOME}/conf/flink-conf.yaml` - Flink cluster configuration

## Dependencies

Required JARs in `${FLINK_HOME}/lib/`:
- `iceberg-flink-runtime-1.20-1.7.1.jar` (32MB)
- `hadoop-common-3.3.4.jar` (4.3MB)
- `hadoop-auth-3.3.4.jar` (102KB)
- `hadoop-hdfs-client-3.3.4.jar` (5.3MB)
- `hadoop-aws-3.3.4.jar` (941KB)
- `hadoop-shaded-guava-1.1.1.jar` (3.3MB)
- `commons-configuration2-2.10.1.jar`
- `woodstox-core-6.7.0.jar` (1.6MB)
- `stax2-api-4.2.2.jar` (192KB)
- `aws-java-sdk-bundle-1.12.262.jar` (268MB)

Total: ~315MB of dependencies

## Next Steps

- Scale up to CloudTrail events with real schema
- Add timestamp conversion utilities
- Implement time-based partitioning
- Add data quality checks
- Set up continuous streaming jobs
