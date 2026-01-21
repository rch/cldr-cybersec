# Java Flink Iceberg Integration - Summary

## Changes Made

### 1. Added Iceberg Dependencies to Parent POM

**File**: `flink-cyber/pom.xml`

Added version properties:
- `iceberg.version`: 1.7.0
- `aws.sdk.version`: 2.20.0

Added dependencies to `<dependencyManagement>`:
- `org.apache.iceberg:iceberg-flink-runtime-1.20:1.7.0`
- `org.apache.iceberg:iceberg-core:1.7.0`
- `org.apache.iceberg:iceberg-aws:1.7.0`
- `software.amazon.awssdk:s3:2.20.0`
- `software.amazon.awssdk:glue:2.20.0`

### 2. Updated flink-common Module

**File**: `flink-cyber/flink-common/pom.xml`

Added dependencies:
- Flink Table API (`flink-table-api-java-bridge`)
- Flink Table Planner (`flink-table-planner_2.12`)
- All Iceberg dependencies

### 3. Created Java Integration Classes

#### CloudTrailIcebergWriter.java
**Location**: `flink-cyber/flink-common/src/main/java/com/cloudera/cyber/flink/iceberg/`

Utility class providing:
- Iceberg catalog configuration with PostgreSQL backend
- CloudTrail table schema creation
- DataStream to Iceberg table writing
- S3/MinIO configuration

#### CloudTrailDataGenIcebergJob.java
**Location**: `flink-cyber/flink-common/src/main/java/com/cloudera/cyber/flink/iceberg/`

Standalone Flink job that:
- Uses Flink DataGen connector for synthetic CloudTrail events
- Writes directly to Iceberg tables in MinIO
- Supports configurable event generation rate
- Uses PostgreSQL catalog for metadata

### 4. Documentation

**File**: `flink-cyber/flink-common/ICEBERG_INTEGRATION.md`

Complete guide covering:
- Architecture overview
- Building and running instructions
- Parameter reference
- Verification steps
- Troubleshooting
- Next steps

## Build Status

✅ **flink-common builds successfully** with all Iceberg dependencies

JAR location: `flink-cyber/flink-common/target/flink-common-2.4.0.jar`

## How to Run

```bash
# Build the project
cd flink-cyber
mvn clean install -DskipTests

# Submit to Flink (ensure devenv up is running)
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

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Flink Job (Java)                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  DataGen     │───▶│  Table API   │───▶│   Iceberg    │  │
│  │  Connector   │    │              │    │  Connector   │  │
│  └──────────────┘    └──────────────┘    └──────┬───────┘  │
└───────────────────────────────────────────────────┼─────────┘
                                                    │
                         ┌──────────────────────────┴──────────────┐
                         │                                         │
                         ▼                                         ▼
              ┌─────────────────────┐                   ┌─────────────────┐
              │   PostgreSQL        │                   │     MinIO       │
              │   (Catalog)         │                   │  (Data Files)   │
              │                     │                   │                 │
              │ • Table metadata    │                   │ • Parquet files │
              │ • Schema versions   │                   │ • Manifests     │
              │ • Snapshots         │                   │ • Metadata      │
              └─────────────────────┘                   └─────────────────┘
```

## Advantages Over Python PyIceberg

1. **Native Integration**: Flink's Iceberg connector provides native support
2. **True Streaming**: No intermediate files needed
3. **Exactly-Once**: Flink checkpoints ensure data consistency
4. **Better Performance**: Direct writes without Python overhead
5. **Metadata Management**: Proper Iceberg snapshot/manifest handling
6. **Schema Evolution**: Built-in support for table schema changes
7. **Production Ready**: Battle-tested connector used in production

## Next Steps

1. **Replace DataGen with Real Data**: Connect to actual CloudTrail stream
2. **Add Partitioning**: Partition by date for query performance
3. **Implement Compaction**: Periodic small file compaction
4. **Add Monitoring**: Export metrics to Prometheus
5. **Create Integration Tests**: End-to-end tests with Testcontainers
6. **Add Schema Registry**: Integrate with Confluent Schema Registry

## Files Modified

- `flink-cyber/pom.xml` - Added Iceberg dependencies
- `flink-cyber/flink-common/pom.xml` - Added Table API and Iceberg deps
- Created: `CloudTrailIcebergWriter.java`
- Created: `CloudTrailDataGenIcebergJob.java`
- Created: `ICEBERG_INTEGRATION.md`
