# Local Flink Environment

Running Flink jobs locally for development and testing.

## Flink Provisioning

### Option A: Build from Source (Recommended)

```bash
cd thirdparty/flink
git submodule update --init --recursive
mvn clean install -DskipTests -Dfast
```

This builds Flink 1.20.1 with Iceberg 1.9.0 compatibility.

### Option B: Use Existing Installation

```bash
cybersec bootstrap settings --set flink_home=/path/to/flink-1.20.1
```

## Running Jobs

### Python (PyFlink)

```bash
# DataGen job
uv run python flink_jobs/cloudtrail_datagen.py

# Processing job
uv run python flink_jobs/cloudtrail_processor.py
```

### Java (Cyber Toolkit)

```bash
cd flink-cyber
mvn clean package -DskipTests

# Submit to local cluster
$FLINK_HOME/bin/flink run \
  -c com.cloudera.cyber.parser.ParserJob \
  parser-chains-flink/target/parser-chains-flink-*.jar \
  --config config/parser-chain.yaml
```

## Local Cluster Mode

For multi-job testing, start a local cluster:

```bash
# Start cluster
$FLINK_HOME/bin/start-cluster.sh

# Access Web UI
open http://localhost:8081

# Stop cluster
$FLINK_HOME/bin/stop-cluster.sh
```

## Checkpointing

For local development, use filesystem checkpointing:

```python
env.get_checkpoint_config().set_checkpoint_storage_dir(
    "file:///tmp/flink-checkpoints"
)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLINK_HOME` | Auto-detected | Flink installation path |
| `ICEBERG_CATALOG_URI` | `postgresql://...` | Catalog connection |
| `S3_ENDPOINT` | `http://localhost:9010` | MinIO endpoint |

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
