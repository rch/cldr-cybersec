# Lakehouse Optimizer

## Overview

Cloudera Lakehouse Optimizer handles table maintenance for Iceberg tables on-prem:

- **Compaction**: Merge small files into optimal sizes
- **Snapshot expiration**: Clean up old snapshots
- **Orphan file deletion**: Remove unreferenced data files
- **Sort optimization**: Rewrite data for better query performance

```d2
direction: down

Tables: {
  label: "Iceberg Tables"
  direction: right

  cloudtrail: {
    label: "cloudtrail_events"
    shape: cylinder
  }
  enrichments: {
    label: "enrichment_tables"
    shape: cylinder
  }
}

Optimizer: {
  label: "Lakehouse Optimizer"
  direction: right

  analyzer: "Table\nAnalyzer"
  planner: "Optimization\nPlanner"
  executor: "Job\nExecutor"
}

Operations: {
  label: "Optimization Operations"
  direction: right

  compact: "Compaction"
  expire: "Snapshot\nExpiration"
  orphan: "Orphan File\nDeletion"
  sort: "Sort\nOptimization"
}

Tables.cloudtrail -> Optimizer.analyzer
Tables.enrichments -> Optimizer.analyzer
Optimizer.analyzer -> Optimizer.planner
Optimizer.planner -> Optimizer.executor
Optimizer.executor -> Operations.compact
Optimizer.executor -> Operations.expire
Optimizer.executor -> Operations.orphan
Optimizer.executor -> Operations.sort
Operations.compact -> Tables.cloudtrail
Operations.expire -> Tables.cloudtrail
Operations.orphan -> Tables.cloudtrail
Operations.sort -> Tables.cloudtrail
```

## Why Disable AWS Optimizer

Running two optimizers on shared Iceberg tables causes:

1. **Conflicting compaction**: Both try to merge same files
2. **Snapshot race conditions**: One expires what other needs
3. **Orphan file deletion errors**: One deletes files other references
4. **Metadata divergence**: Table state becomes inconsistent

### Solution

- **AWS**: Disable all Glue Iceberg optimization
- **On-prem**: Lakehouse Optimizer is sole optimizer
- **AWS tables**: Remain queryable but not optimized locally

## Configuration

### Lakehouse Optimizer Service

```yaml
# lakehouse-optimizer-config.yaml
optimization:
  # Global settings
  enabled: true
  schedule: "0 */4 * * *"  # Every 4 hours

  # Table-specific overrides
  tables:
    - database: cybersec
      table: cloudtrail_events
      compaction:
        enabled: true
        targetFileSizeMb: 512
        minFilesToCompact: 10
        maxConcurrentJobs: 4
      snapshotExpiration:
        enabled: true
        retainLast: 100
        maxAgeHours: 168  # 7 days
      orphanFileDeletion:
        enabled: true
        olderThanHours: 72
      sortOptimization:
        enabled: true
        sortOrder:
          - column: event_time
            direction: asc
          - column: aws_region
            direction: asc

    - database: cybersec
      table: "*"  # Default for other tables
      compaction:
        enabled: true
        targetFileSizeMb: 256
```

### CLI Operations

```bash
# Check table optimization status
lakehouse-optimizer status cybersec.cloudtrail_events

# Trigger immediate compaction
lakehouse-optimizer compact cybersec.cloudtrail_events \
  --target-file-size 512MB \
  --partial-progress

# Expire old snapshots
lakehouse-optimizer expire-snapshots cybersec.cloudtrail_events \
  --retain-last 50 \
  --older-than "7 days"

# Delete orphan files
lakehouse-optimizer remove-orphans cybersec.cloudtrail_events \
  --older-than "3 days" \
  --dry-run

# Rewrite for sort order
lakehouse-optimizer sort cybersec.cloudtrail_events \
  --sort-order "event_time ASC, aws_region ASC" \
  --filter "event_time >= '2026-01-01'"
```

## Optimization Strategies

### Compaction Strategy

```d2
direction: right

Before: {
  label: "Before Compaction"

  f1: "5MB" {shape: document}
  f2: "12MB" {shape: document}
  f3: "3MB" {shape: document}
  f4: "8MB" {shape: document}
  f5: "2MB" {shape: document}
  f6: "15MB" {shape: document}
  f7: "6MB" {shape: document}
  f8: "4MB" {shape: document}
}

After: {
  label: "After Compaction"

  c1: "512MB" {shape: document}
}

Before -> After: "Merge 8 files\ninto 1"
```

For CloudTrail data:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Target file size | 512MB | Optimal for Parquet + Impala |
| Min files to compact | 10 | Avoid compacting already-large files |
| Max concurrent jobs | 4 | Balance with query workload |
| Schedule | Every 4 hours | Catch up with replication lag |

### Snapshot Retention

For replicated tables, retain more snapshots:

- **Retain last 100**: Allow replication to catch up
- **Max age 7 days**: Compliance requirement
- **Never delete during replication**: Check replication status first

### Sort Optimization

Periodically rewrite data sorted by query patterns:

```sql
-- Most common query pattern
SELECT * FROM cloudtrail_events
WHERE event_time BETWEEN '2026-01-01' AND '2026-01-31'
  AND aws_region = 'us-east-1'
  AND event_source = 'iam.amazonaws.com'
ORDER BY event_time;
```

Sort order: `event_time ASC, aws_region ASC, event_source ASC`

## Monitoring

### Metrics Dashboard

| Metric | Target | Alert |
|--------|--------|-------|
| File count per partition | < 100 | > 500 |
| Avg file size | > 256MB | < 50MB |
| Snapshot count | < 100 | > 200 |
| Orphan file size | < 1GB | > 10GB |
| Compaction duration | < 30 min | > 2 hours |

### Hive Metastore Queries

```sql
-- Check file count by partition
SELECT
  t.tbl_name,
  p.part_name,
  count(*) as file_count,
  sum(f.file_size_in_bytes) / 1024 / 1024 as total_mb
FROM iceberg_files f
JOIN partitions p ON f.partition_id = p.id
JOIN tables t ON p.table_id = t.id
WHERE t.tbl_name = 'cloudtrail_events'
GROUP BY t.tbl_name, p.part_name
HAVING count(*) > 100
ORDER BY file_count DESC;
```
