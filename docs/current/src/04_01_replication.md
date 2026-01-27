# Replication Strategy

## Replication Architecture

```d2
direction: right

AWS: {
  label: "AWS Source"

  s3_iceberg: {
    label: "S3 Iceberg Tables\n(cloudtrail_events)"
    shape: cylinder
  }

  metadata: {
    label: "Iceberg Metadata"
    shape: document
  }

  data: {
    label: "Parquet Data Files"
    shape: document
  }

  s3_iceberg -> metadata
  s3_iceberg -> data
}

Replication: {
  label: "Replication Manager"

  scheduler: "Snapshot\nScheduler"
  differ: "Incremental\nDiff Engine"
  transfer: "Data\nTransfer"
  commit: "Metadata\nCommit"
}

OnPrem: {
  label: "On-Prem Target"

  ozone_iceberg: {
    label: "Ozone Iceberg Tables\n(cloudtrail_events)"
    shape: cylinder
  }

  metadata: {
    label: "Iceberg Metadata"
    shape: document
  }

  data: {
    label: "Parquet Data Files"
    shape: document
  }

  ozone_iceberg -> metadata
  ozone_iceberg -> data
}

AWS.metadata -> Replication.scheduler: "Poll snapshots"
Replication.scheduler -> Replication.differ
Replication.differ -> Replication.transfer: "New files"
AWS.data -> Replication.transfer: "Copy"
Replication.transfer -> OnPrem.data
Replication.commit -> OnPrem.metadata
```

## Replication Modes

### 1. Snapshot-Based Replication

Replicate complete Iceberg snapshots for consistency:

```python
# replication_job.py
from pyiceberg.catalog import load_catalog

def replicate_snapshot(source_catalog, target_catalog, table_name):
    """Replicate latest snapshot from AWS to on-prem."""

    source_table = source_catalog.load_table(table_name)
    target_table = target_catalog.load_table(table_name)

    # Get latest source snapshot
    source_snapshot = source_table.current_snapshot()

    # Get current target snapshot
    target_snapshot = target_table.current_snapshot()

    if source_snapshot.snapshot_id == target_snapshot.snapshot_id:
        print("Already in sync")
        return

    # Find new data files since last sync
    new_files = []
    for entry in source_snapshot.manifests:
        manifest = source_table.io.read_manifest(entry)
        for file in manifest.entries:
            if file.snapshot_id > target_snapshot.snapshot_id:
                new_files.append(file)

    # Copy new data files
    for file in new_files:
        copy_s3_to_ozone(file.file_path, target_path)

    # Commit new snapshot to target
    target_table.append(new_files)
```

### 2. Streaming Replication (Near Real-Time)

For lower latency, replicate as Flink writes:

```d2
direction: right

Flink: {
  label: "Flink Job"

  writer: "Iceberg\nWriter"
}

AWS: {
  label: "AWS"

  s3: {
    label: "S3"
    shape: cylinder
  }

  sns: "SNS Topic"
}

OnPrem: {
  label: "On-Prem"

  listener: "Event\nListener"
  copier: "File\nCopier"
  ozone: {
    label: "Ozone"
    shape: cylinder
  }
}

Flink.writer -> AWS.s3: "Write"
AWS.s3 -> AWS.sns: "Object created"
AWS.sns -> OnPrem.listener: "Notify"
OnPrem.listener -> OnPrem.copier: "Trigger"
AWS.s3 -> OnPrem.copier: "Copy" {style.stroke-dash: 5}
OnPrem.copier -> OnPrem.ozone: "Write"
```

## Configuration

### Cloudera Replication Manager Policy

```yaml
# replication-policy.yaml
apiVersion: replication.cloudera.com/v1
kind: IcebergReplicationPolicy
metadata:
  name: cloudtrail-aws-to-onprem
spec:
  source:
    type: aws-s3
    bucket: cybersec-cloudtrail-iceberg-123456789012
    region: us-east-1
    credentials:
      secretRef: aws-replication-creds
    catalog:
      type: glue
      database: cybersec

  target:
    type: ozone
    bucket: cybersec
    path: /iceberg/warehouse
    catalog:
      type: hive
      database: cybersec

  tables:
    - name: cloudtrail_events
      partitionFilter: "event_time >= current_date - interval 90 days"

  schedule:
    interval: 15m
    retryPolicy:
      maxRetries: 3
      backoff: exponential

  bandwidth:
    limit: 1Gbps
    throttleOnPeak: true
    peakHours: "09:00-17:00"

  verification:
    enabled: true
    checksum: true
    rowCount: true
```

## Monitoring

### Replication Metrics

| Metric | Alert Threshold | Description |
|--------|-----------------|-------------|
| `replication_lag_seconds` | > 900 (15 min) | Time since last successful sync |
| `replication_bytes_pending` | > 10GB | Data waiting to replicate |
| `replication_errors_count` | > 0 | Failed file transfers |
| `snapshot_diff_files` | > 1000 | Files in pending snapshot |

### Lag Dashboard Query

```sql
-- Compare event counts between AWS and on-prem
WITH aws_counts AS (
  SELECT
    date_trunc('hour', event_time) as event_hour,
    count(*) as aws_count
  FROM aws_catalog.cybersec.cloudtrail_events
  WHERE event_time >= current_timestamp - interval '24' hour
  GROUP BY 1
),
onprem_counts AS (
  SELECT
    date_trunc('hour', event_time) as event_hour,
    count(*) as onprem_count
  FROM hive.cybersec.cloudtrail_events
  WHERE event_time >= current_timestamp - interval '24' hour
  GROUP BY 1
)
SELECT
  COALESCE(a.event_hour, o.event_hour) as event_hour,
  COALESCE(a.aws_count, 0) as aws_count,
  COALESCE(o.onprem_count, 0) as onprem_count,
  COALESCE(a.aws_count, 0) - COALESCE(o.onprem_count, 0) as diff
FROM aws_counts a
FULL OUTER JOIN onprem_counts o ON a.event_hour = o.event_hour
ORDER BY event_hour DESC;
```

## Conflict Resolution

### Write Conflict Handling

Since AWS is the source of truth for new data:

1. **On-prem is read-only** for replicated tables
2. **Local enrichments** written to separate tables
3. **Merge on read** via Impala views if needed

```sql
-- View that merges AWS data with local enrichments
CREATE VIEW cybersec.cloudtrail_enriched AS
SELECT
  ct.*,
  e.threat_score,
  e.ioc_match
FROM cybersec.cloudtrail_events ct
LEFT JOIN cybersec.local_enrichments e
  ON ct.event_id = e.event_id;
```
