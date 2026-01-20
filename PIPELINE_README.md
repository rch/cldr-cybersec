# CloudTrail Event Processing Pipeline

A complete data pipeline for generating, processing, and storing AWS CloudTrail events using Apache Flink, Apache Iceberg, and PostgreSQL.

## Architecture

```
┌─────────────────┐
│ Flink DataGen   │ Generates synthetic CloudTrail events
│ (Python)        │
└────────┬────────┘
         │ Kafka: cloudtrail-raw
         ▼
┌─────────────────┐
│ Flink Processor │ Parses and enriches events
│ (Python)        │
└────────┬────────┘
         │ Kafka: cloudtrail-parsed
         ▼
┌─────────────────┐
│ PyIceberg       │ Persists to Iceberg format
│ Writer          │ PostgreSQL catalog + MinIO storage
└─────────────────┘
```

## Components

### 1. Flink DataGen (`flink_jobs/cloudtrail_datagen.py`)
- Generates realistic AWS CloudTrail events
- Configurable event rate (default: 10 events/second)
- Supports multiple event types: ConsoleLogin, S3 operations, EC2 actions, IAM changes
- Outputs to Kafka topic: `cloudtrail-raw`

### 2. Flink Processor (`flink_jobs/cloudtrail_processor.py`)
- Parses CloudTrail JSON events
- Extracts key fields: event metadata, user identity, source IP, region
- Enriches with processing timestamps
- Outputs to Kafka topic: `cloudtrail-parsed`

### 3. PyIceberg Writer (`iceberg_writer/cloudtrail_writer.py`)
- Consumes parsed events from Kafka
- Writes to Iceberg tables in Parquet format
- Uses PostgreSQL as the Iceberg catalog
- Stores data in MinIO (S3-compatible storage)
- Partitioned by event date for efficient querying

### 4. Query Interface (`iceberg_writer/cloudtrail_query.py`)
- Convenient Python API for querying CloudTrail data
- Time-based queries (last N hours)
- Filter by event name, source IP, account ID
- Statistics and aggregations

## Prerequisites

All services are managed via `devenv`:
- ✅ Apache Flink (JobManager + TaskManager)
- ✅ PostgreSQL 16 with pg_cron and Apache AGE extensions
- ✅ MinIO (S3-compatible object storage)
- ✅ Python 3.12+ with dependencies:
  - `apache-flink>=2.2.0` - Apache Flink Python API
  - `pyiceberg[s3fs,sql-postgres]>=0.10.0` - Iceberg with S3 and PostgreSQL support
  - `kafka-python>=2.0.2` - Kafka consumer for Python
  - `pyarrow>=15.0.0` - Arrow data format
  - `pandas>=2.2.0` - Data manipulation

## Quick Start

### 1. Start Services

```bash
# Start all services (Flink, PostgreSQL, MinIO)
devenv up
```

This starts:
- **Flink Web UI**: http://localhost:8081
- **MinIO Console**: http://localhost:9011 (minioadmin/minioadmin)
- **PostgreSQL**: localhost:5438

### 2. Initialize the Environment

```bash
# Check service status and initialize Iceberg catalog
python main.py
```

### 3. Run the Pipeline

Open three separate terminals:

**Terminal 1 - Data Generator:**
```bash
python flink_jobs/cloudtrail_datagen.py
```

**Terminal 2 - Event Processor:**
```bash
python flink_jobs/cloudtrail_processor.py
```

**Terminal 3 - Iceberg Writer:**
```bash
python iceberg_writer/cloudtrail_writer.py
```

### 4. Query the Data

```bash
# Run example queries
python iceberg_writer/cloudtrail_query.py
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ICEBERG_CATALOG_URI` | `postgresql://postgres@localhost:5438/cybersec` | PostgreSQL connection for Iceberg catalog |
| `ICEBERG_WAREHOUSE` | `s3://cybersec/iceberg/warehouse` | S3/MinIO path for Iceberg data |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka bootstrap servers |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | MinIO/S3 access key |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin` | MinIO/S3 secret key |
| `S3_ENDPOINT` | `http://localhost:9010` | MinIO endpoint |

### Iceberg Table Schema

The CloudTrail events table (`cybersec.cloudtrail_events`) has the following schema:

```
event_id              STRING
event_version         STRING
event_timestamp       TIMESTAMP
event_source          STRING
event_name            STRING
aws_region            STRING
source_ip             STRING
user_agent            STRING
user_type             STRING
user_arn              STRING
account_id            STRING
read_only             BOOLEAN
event_type            STRING
processing_time       TIMESTAMP
```

**Partitioning**: By `event_date` (day transform on `event_timestamp`)  
**Sorting**: By `event_timestamp`, `event_id`

## Development

### Install Dependencies
Install dependencies (managed by uv)
uv sync

# Key dependencies:
# - apache-flink: Python API for Apache Flink
# - pyiceberg[s3fs,sql-postgres]: Iceberg with S3fs and PostgreSQL catalog support
# - kafka-python: Kafka consumer library
# devenv handles Python environment automatically
devenv shell

# Dependencies are installed via pyproject.toml
uv sync
```

### Project Structure

```
cybersec/
├── devenv.nix              # Dev environment configuration
├── pyproject.toml          # Python dependencies
├── main.py                 # Pipeline orchestrator
├── flink_jobs/             # Flink jobs
│   ├── cloudtrail_datagen.py
│   └── cloudtrail_processor.py
├── iceberg_writer/         # PyIceberg components
│   ├── cloudtrail_writer.py
│   └── cloudtrail_query.py
└── flink-cyber/            # Java cybersec toolkit
```

## Querying Data

### Python API

```python
from iceberg_writer.cloudtrail_query import CloudTrailQuery

query = CloudTrailQuery(
    catalog_uri="postgresql://postgres@localhost:5438/cybersec",
    warehouse_path="s3://cybersec/iceberg/warehouse"
)

# Get recent events
events = query.query_recent_events(hours=24, limit=1000)

# Query by event name
console_logins = query.query_by_event_name("ConsoleLogin")

# Get statistics
stats = query.get_event_statistics(hours=24)
print(stats)
```

### DuckDB Integration

```python
import duckdb

con = duckdb.connect()
con.execute("""
    INSTALL iceberg;
    LOAD iceberg;
    
    SELECT event_name, COUNT(*) as count
    FROM iceberg_scan('s3://cybersec/iceberg/warehouse/cybersec/cloudtrail_events')
    WHERE event_timestamp > NOW() - INTERVAL '1 hour'
    GROUP BY event_name
    ORDER BY count DESC
""")
```

### Apache Spark

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .config("spark.sql.catalog.cybersec", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.cybersec.type", "jdbc") \
    .config("spark.sql.catalog.cybersec.uri", "jdbc:postgresql://localhost:5438/cybersec") \
    .getOrCreate()

df = spark.table("cybersec.cloudtrail_events")
df.filter("event_name = 'ConsoleLogin'").show()
```

## Monitoring

### Flink Dashboard
- URL: http://localhost:8081
- Monitor job status, metrics, and task managers
- View job execution plans and checkpoints

### MinIO Console
- URL: http://localhost:9011
- Credentials: `minioadmin` / `minioadmin`
- Browse Iceberg data files and metadata

### PostgreSQL
```bash
# Connect to catalog database
psql -h localhost -p 5438 -U postgres -d cybersec

# View Iceberg tables
\dt iceberg.*

# Query catalog metadata
SELECT * FROM iceberg.catalog_tables;
```

## Troubleshooting

### Services Not Starting

```bash
# Check devenv status
devenv info

# Restart services
devenv down
devenv up
```

### Kafka Connection Issues

```bash
# Verify Kafka is running
nc -zv localhost 9092

# Check Kafka topics
kafka-topics.sh --bootstrap-server localhost:9092 --list
```

### Iceberg Catalog Issues

```bash
# Reinitialize catalog
python main.py init

# Check PostgreSQL connectivity
psql -h localhost -p 5438 -U postgres -d cybersec -c "SELECT 1"
```

## Performance Tuning

### Flink Configuration

Edit [devenv.nix](devenv.nix) to adjust Flink settings:

```nix
taskmanager.numberOfTaskSlots=8  # Increase parallelism
jobmanager.memory.process.size=2g
taskmanager.memory.process.size=4g
```

### Iceberg Writer Batch Size

Adjust in [cloudtrail_writer.py](iceberg_writer/cloudtrail_writer.py):

```python
writer.write_from_kafka(
    batch_size=5000  # Larger batches = fewer writes
)
```

## Security Considerations

⚠️ **This is a development setup**. For production:

1. **Enable authentication** on all services
2. **Use TLS/SSL** for communication
3. **Rotate credentials** regularly
4. **Implement proper IAM** for S3/MinIO access
5. **Enable Flink security** features
6. **Use secrets management** for credentials

## Contributing

The cybersec toolkit includes extensive Java-based parsers and enrichment capabilities in the `flink-cyber/` directory. Contributions are welcome!

## License

See [LICENSE](LICENSE) and [NOTICE](NOTICE) files.

## Resources

- [Apache Flink Documentation](https://flink.apache.org/)
- [Apache Iceberg Documentation](https://iceberg.apache.org/)
- [PyIceberg Documentation](https://py.iceberg.apache.org/)
- [AWS CloudTrail Event Reference](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference.html)
