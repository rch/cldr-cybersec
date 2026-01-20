# CloudTrail Event Processing Pipeline - Quick Reference

## Start Everything

```bash
# Terminal 1: Start services
devenv up

# Terminal 2: Initialize and check status
python main.py

# Terminal 3: Start data generator
python flink_jobs/cloudtrail_datagen.py

# Terminal 4: Start processor
python flink_jobs/cloudtrail_processor.py

# Terminal 5: Start Iceberg writer
python iceberg_writer/cloudtrail_writer.py

# Terminal 6: Query data
python iceberg_writer/cloudtrail_query.py
```

## Service URLs

- **Flink Dashboard**: http://localhost:8081
- **MinIO Console**: http://localhost:9011 (minioadmin/minioadmin)
- **PostgreSQL**: `psql -h localhost -p 5438 -U postgres -d cybersec`

## Common Commands

### Check Services
```bash
python main.py check
```

### Initialize Iceberg Catalog
```bash
python main.py init
```

### View Status
```bash
python main.py status
```

## Pipeline Flow

```
CloudTrail DataGen → Kafka (cloudtrail-raw) 
  → Flink Processor → Kafka (cloudtrail-parsed)
  → Iceberg Writer → PostgreSQL Catalog + MinIO Storage
```

## Query Data

```python
from iceberg_writer.cloudtrail_query import CloudTrailQuery

query = CloudTrailQuery(
    "postgresql://postgres@localhost:5438/cybersec",
    "s3://cybersec/iceberg/warehouse"
)

# Recent events
events = query.query_recent_events(hours=24)

# Statistics
stats = query.get_event_statistics(hours=24)
```

## Troubleshooting

**Services not running?**
```bash
devenv up
```

**Iceberg catalog not initialized?**
```bash
python main.py init
```

**No data appearing?**
- Check Flink dashboard for job status
- Verify Kafka topics exist
- Check logs in terminal windows

## Dependencies

All managed by devenv:
- Flink 1.18+
- PostgreSQL 16
- MinIO
- Python 3.12+
- **apache-flink** (Flink Python API)
- **pyiceberg[s3fs,sql-postgres]** (Iceberg with S3 and PostgreSQL support)
- **kafka-python** (Kafka consumer)
- PyArrow, Pandas

## Architecture

```
┌──────────────┐
│ Flink DataGen│ Generates synthetic CloudTrail events (10/sec)
└──────┬───────┘
       │ Kafka: cloudtrail-raw
       ▼
┌──────────────┐
│Flink Processor│ Parses JSON, extracts fields, enriches
└──────┬───────┘
       │ Kafka: cloudtrail-parsed
       ▼
┌──────────────┐
│PyIceberg Write│ Batches and writes Parquet files
└──────┬───────┘
       │
       ├─► PostgreSQL (catalog metadata)
       └─► MinIO (Parquet data files)
```

## Key Features

✅ Synthetic CloudTrail event generation  
✅ Real-time event parsing and enrichment  
✅ Iceberg table format with time partitioning  
✅ PostgreSQL catalog for ACID transactions  
✅ S3-compatible storage (MinIO)  
✅ Python query interface  
✅ Compatible with Spark, DuckDB, Trino  

## Next Steps

1. Customize event generation rates
2. Add custom enrichments
3. Integrate with cybersec toolkit parsers
4. Connect to BI tools (Superset, Metabase)
5. Add alerting and monitoring
