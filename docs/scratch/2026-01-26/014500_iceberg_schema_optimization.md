# Iceberg Schema Optimization Analysis

**Date:** 2026-01-26
**Table:** `default.cloudtrail_events`

## Current State

### Table Metrics
| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Total Records | 732,450 | - | OK |
| Total Files | 7,324 | 100-500 | CRITICAL |
| Total Size | 87 MB | - | OK |
| Avg File Size | 12 KB | 128 MB | CRITICAL |
| Snapshots | 7,324 | < 50 | CRITICAL |
| Manifests | 97 | < 10 | WARNING |
| Partitioned | No | Yes | NEEDS WORK |
| Sort Order | No | Yes | NEEDS WORK |

### Schema
```
event_data: string (JSON blob)
event_time: timestamp
```

### Query Patterns (from iceberg_browser.py)
1. **Time-range filtering**: `event_time >= start AND event_time <= end`
2. **Event type filtering**: `event_name`, `event_source` (parsed from JSON)
3. **Region filtering**: `awsRegion` (parsed from JSON)
4. **Aggregations**: `GROUP BY region, event_source`
5. **Limit scans**: All queries use `limit=10000-50000`

## RETE Rule Analysis

### Triggered Rules
| Priority | Rule | Action |
|----------|------|--------|
| 900 | `alert_full_table_scan` | Add partition filters |
| 800 | `compact_small_files` | Compact to 128MB files |
| 750 | `compact_many_files_per_partition` | Reduce file count |
| 400 | `expire_old_snapshots` | Expire snapshots > 7 days |

### Missing Optimizations (rules exist but thresholds not met)
- `suggest_daily_partitioning`: Needs row_count > 1M (currently 732K)
- `suggest_sort_order`: Needs row_count > 1M

## Proposed Optimizations

### 1. Schema Denormalization (HIGH PRIORITY)

**Current:** Single JSON blob requiring runtime parsing
```sql
SELECT * FROM cloudtrail_events
-- Then parse JSON in Python for every row
```

**Proposed:** Columnar schema with commonly-queried fields
```sql
CREATE TABLE cloudtrail_events_v2 (
  -- Identity fields
  event_id STRING,
  event_time TIMESTAMP,

  -- Frequently filtered fields (from query analysis)
  event_name STRING,
  event_source STRING,
  aws_region STRING,
  user_identity_arn STRING,
  source_ip STRING,

  -- Semi-structured data (less frequently filtered)
  request_parameters STRING,  -- JSON
  response_elements STRING,   -- JSON

  -- Original blob for completeness
  raw_event STRING
)
```

**Benefits:**
- Column statistics enable predicate pushdown
- No JSON parsing at query time
- Parquet column pruning (only read needed columns)
- Better compression (similar values grouped)

### 2. Partitioning Strategy (HIGH PRIORITY)

**Analysis of time distribution:**
- Data spans ~21 hours
- ~35K events/hour average
- Time-range queries are common

**Recommended:** Hourly partitioning using hidden partitioning
```python
# PyIceberg schema evolution
table.update_spec() \
    .add_field("hour", HourTransform(), "event_time") \
    .commit()
```

**Why hourly vs daily:**
- With 35K events/hour, daily partitions would have 840K rows
- Hourly gives 35K rows/partition - better for targeted queries
- Aligns with typical security investigation windows

### 3. Compaction Strategy (CRITICAL)

**Immediate action:** Compact 7,324 files → ~1 file
```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog("cybersec", **config)
table = catalog.load_table("default.cloudtrail_events")

# Rewrite all files with target size 512MB (data is only 87MB total)
table.rewrite_data_files(
    target_file_size_bytes=512 * 1024 * 1024,
    max_file_size_bytes=1024 * 1024 * 1024,
)
```

**Post-partitioning:** Maintain 1-5 files per partition
- Configure writer: `write.target-file-size-bytes: 134217728` (128MB)

### 4. Snapshot Management (HIGH PRIORITY)

**Current state:** 7,324 snapshots (1 per write)

**Immediate cleanup:**
```python
from datetime import datetime, timedelta

table.expire_snapshots() \
    .older_than(datetime.now() - timedelta(days=1)) \
    .commit()
```

**Ongoing strategy:**
- Keep last 10 snapshots for time-travel
- Expire snapshots older than 24 hours
- Consider cherry-pick commits for streaming writes

### 5. Sort Order (MEDIUM PRIORITY)

**Recommended:** Sort by event_time within partitions
```python
table.replace_sort_order() \
    .asc("event_time") \
    .commit()
```

**Benefits:**
- Range queries on event_time scan fewer row groups
- Better data locality for time-series access patterns

## Implementation Order

1. **Expire snapshots** - Immediate, reduces metadata overhead
2. **Compact files** - Immediate, massive query improvement
3. **Denormalize schema** - Create new table with columnar schema
4. **Add partitioning** - Apply to new table
5. **Add sort order** - Apply during compaction
6. **Migrate data** - INSERT INTO new_table SELECT FROM old_table
7. **Update queries** - Point to new table

## Heuristics for In-Situ Optimization

### Captured Heuristics

```python
ICEBERG_OPTIMIZATION_HEURISTICS = {
    # File size thresholds
    "min_file_size_mb": 32,      # Below this, needs compaction
    "target_file_size_mb": 128,  # Ideal file size
    "max_file_size_mb": 512,     # Above this, may need splitting

    # File count thresholds
    "files_per_partition_max": 20,    # Compact if exceeded
    "files_per_partition_target": 5,  # Ideal count

    # Snapshot thresholds
    "max_snapshots": 50,              # Expire older if exceeded
    "snapshot_retention_hours": 24,   # For streaming tables
    "snapshot_retention_days": 7,     # For batch tables

    # Partition heuristics
    "partition_row_count_min": 10_000,    # Below: too fine-grained
    "partition_row_count_target": 100_000, # Ideal rows per partition
    "partition_row_count_max": 1_000_000,  # Above: consider finer grain

    # Schema heuristics
    "json_column_query_threshold": 0.5,  # If >50% queries parse JSON, denormalize
    "column_cardinality_partition_threshold": 1000,  # Don't partition on high-cardinality

    # Query pattern thresholds
    "full_scan_row_threshold": 100_000,  # Alert on full scan above this
    "scan_efficiency_threshold": 0.1,    # Alert if scanning >10% of table for point query
}
```

### Detection Rules for In-Situ Optimizer

```python
# Rule: Detect streaming write pattern
if snapshots_per_hour > 10:
    suggest("Enable snapshot coalescing or batch writes")
    suggest("Set write.wap.enabled=true for write-audit-publish")

# Rule: Detect JSON anti-pattern
if query_parses_json_column and query_frequency > 10/hour:
    suggest(f"Denormalize {json_column} into separate columns")

# Rule: Detect partition mismatch
if partition_count > 0 and avg_rows_per_partition < 10_000:
    suggest("Coarser partitioning - partitions too small")
if partition_count > 0 and avg_rows_per_partition > 1_000_000:
    suggest("Finer partitioning - partitions too large")

# Rule: Detect missing time partitioning
if has_timestamp_column and uses_time_range_queries and not partitioned_by_time:
    suggest(f"Add time partitioning: days({timestamp_column}) or hours({timestamp_column})")

# Rule: Detect sort order opportunity
if range_query_columns and not has_sort_order:
    suggest(f"Add sort order on {range_query_columns[0]}")
```

## Expected Impact

| Optimization | Metric | Before | After | Improvement |
|--------------|--------|--------|-------|-------------|
| Compaction | Files scanned | 7,324 | 1 | 7,324x |
| Compaction | Metadata size | 97 manifests | 1 manifest | 97x |
| Expire snapshots | Snapshot count | 7,324 | 10 | 732x |
| Partitioning | Rows scanned (1h query) | 732K | 35K | 21x |
| Denormalization | JSON parsing | Every row | None | ~10x CPU |
| Sort order | Row groups scanned | All | 1-2 | ~5x |

**Combined improvement for typical query:**
- Before: Scan 7,324 files, read all 732K rows, parse JSON for each
- After: Scan 1 file, read 35K rows (1 hour partition), no JSON parsing
- **Estimated: 100-1000x faster**
