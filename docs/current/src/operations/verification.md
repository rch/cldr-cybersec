# Data Verification

## Verification Strategy

```d2
direction: right

AWS: {
  label: "AWS Source"

  iceberg: {
    label: "S3 Iceberg\nTables"
    shape: cylinder
  }

  athena: "Athena\n(Query)"
}

Verification: {
  label: "Verification Layer"

  hourly: "Hourly\nCount Check"
  daily: "Daily\nHash Validation"
  weekly: "Weekly\nFull Reconciliation"
}

OnPrem: {
  label: "On-Prem Target"

  iceberg: {
    label: "Ozone Iceberg\nTables"
    shape: cylinder
  }

  impala: "Impala\n(Query)"
}

AWS.iceberg -> AWS.athena
AWS.athena -> Verification.hourly
AWS.athena -> Verification.daily
AWS.athena -> Verification.weekly

OnPrem.iceberg -> OnPrem.impala
OnPrem.impala -> Verification.hourly
OnPrem.impala -> Verification.daily
OnPrem.impala -> Verification.weekly
```

## Verification Levels

### Level 1: Hourly Count Check

Quick sanity check - counts should match within replication lag:

```sql
-- AWS (Athena)
SELECT
  date_trunc('hour', event_time) as event_hour,
  count(*) as count
FROM cybersec.cloudtrail_events
WHERE event_time >= current_timestamp - interval '24' hour
GROUP BY 1
ORDER BY 1;

-- On-Prem (Impala)
SELECT
  date_trunc('hour', event_time) as event_hour,
  count(*) as count
FROM cybersec.cloudtrail_events
WHERE event_time >= now() - interval 1 day
GROUP BY 1
ORDER BY 1;
```

**Alert threshold**: > 1% difference after 1-hour lag

### Level 2: Daily Hash Validation

Verify data integrity using partition-level checksums:

```python
#!/usr/bin/env python3
"""daily_hash_validation.py - Compare partition hashes between AWS and on-prem."""

import hashlib
from datetime import datetime, timedelta

def compute_partition_hash(catalog, table, partition_date):
    """Compute hash of event_ids in a partition.

    Uses sorted event_ids to create deterministic hash.
    """
    query = f"""
    SELECT event_id
    FROM {table}
    WHERE date(event_time) = '{partition_date}'
    ORDER BY event_id
    """

    results = catalog.execute(query)
    event_ids = [row[0] for row in results]

    # Create hash of sorted event IDs
    hash_input = "\n".join(event_ids).encode()
    return hashlib.sha256(hash_input).hexdigest()

def validate_partition(aws_catalog, onprem_catalog, partition_date):
    """Compare partition hash between AWS and on-prem."""

    aws_hash = compute_partition_hash(
        aws_catalog,
        "cybersec.cloudtrail_events",
        partition_date
    )

    onprem_hash = compute_partition_hash(
        onprem_catalog,
        "cybersec.cloudtrail_events",
        partition_date
    )

    match = aws_hash == onprem_hash

    return {
        "partition_date": str(partition_date),
        "aws_hash": aws_hash[:16] + "...",
        "onprem_hash": onprem_hash[:16] + "...",
        "match": match,
    }

# Validate yesterday's partition
yesterday = datetime.now().date() - timedelta(days=1)
result = validate_partition(aws_cat, onprem_cat, yesterday)
print(f"Partition {result['partition_date']}: {'MATCH' if result['match'] else 'MISMATCH'}")
```

### Level 3: Weekly Full Reconciliation

Detailed comparison to identify specific missing events:

```sql
-- Find events in AWS but not in on-prem
WITH aws_events AS (
  SELECT event_id, event_time, event_source, event_name
  FROM aws_catalog.cybersec.cloudtrail_events
  WHERE event_time >= current_date - interval '7' day
),
onprem_events AS (
  SELECT event_id
  FROM cybersec.cloudtrail_events
  WHERE event_time >= current_date - interval 7 day
)
SELECT
  a.event_id,
  a.event_time,
  a.event_source,
  a.event_name
FROM aws_events a
LEFT JOIN onprem_events o ON a.event_id = o.event_id
WHERE o.event_id IS NULL
ORDER BY a.event_time;
```

## Automated Verification Pipeline

### Airflow DAG

```python
# verification_dag.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.slack.operators.slack_webhook import SlackWebhookOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'data-platform',
    'depends_on_past': False,
    'email_on_failure': True,
    'email': ['data-platform@company.com'],
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'cloudtrail_data_verification',
    default_args=default_args,
    description='Verify CloudTrail data consistency between AWS and on-prem',
    schedule_interval='0 */4 * * *',  # Every 4 hours
    start_date=datetime(2026, 1, 1),
    catchup=False,
)

def verify_hourly_counts(**context):
    """Compare hourly counts between AWS and on-prem."""
    # ... implementation ...
    pass

def verify_daily_hashes(**context):
    """Compare partition hashes for completed days."""
    # ... implementation ...
    pass

def alert_on_mismatch(**context):
    """Send alert if verification fails."""
    ti = context['ti']
    count_result = ti.xcom_pull(task_ids='verify_counts')
    hash_result = ti.xcom_pull(task_ids='verify_hashes')

    if not count_result['match'] or not hash_result['match']:
        return "alert_slack"
    return "skip_alert"

verify_counts = PythonOperator(
    task_id='verify_counts',
    python_callable=verify_hourly_counts,
    dag=dag,
)

verify_hashes = PythonOperator(
    task_id='verify_hashes',
    python_callable=verify_daily_hashes,
    dag=dag,
)

alert_slack = SlackWebhookOperator(
    task_id='alert_slack',
    webhook_token='...',
    message='CloudTrail data verification FAILED - check reconciliation report',
    dag=dag,
)

verify_counts >> verify_hashes >> alert_slack
```

## Reconciliation Reports

### Daily Report Template

```markdown
# CloudTrail Data Verification Report
**Date**: 2026-01-27
**Generated**: 2026-01-27 06:00 UTC

## Summary
| Metric | AWS | On-Prem | Status |
|--------|-----|---------|--------|
| Events (24h) | 1,234,567 | 1,234,502 | OK (0.005% diff) |
| Partitions verified | 7 | 7 | OK |
| Hash matches | 7/7 | - | OK |

## Hourly Breakdown
| Hour (UTC) | AWS Count | On-Prem Count | Diff |
|------------|-----------|---------------|------|
| 00:00 | 51,234 | 51,234 | 0 |
| 01:00 | 48,901 | 48,901 | 0 |
| ... | ... | ... | ... |

## Alerts
None

## Actions Required
None
```

### Missing Event Investigation

When events are missing:

```sql
-- Get details of missing events for investigation
SELECT
  event_time,
  event_source,
  event_name,
  aws_region,
  user_identity.arn as actor,
  source_ip_address
FROM aws_catalog.cybersec.cloudtrail_events
WHERE event_id IN (
  -- List of missing event IDs from reconciliation
  'event-id-1',
  'event-id-2',
  ...
)
ORDER BY event_time;
```

Common causes of missing events:
1. **Replication lag**: Wait and re-check
2. **Network issues**: Check VPN/Direct Connect logs
3. **Flink job failure**: Check job manager logs
4. **Storage issues**: Check Ozone health
