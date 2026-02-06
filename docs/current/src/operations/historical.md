# Historical Data Retrieval

## Retrieval Workflow

```d2
direction: down

Request: {
  label: "1. Request Phase"
  direction: right

  ticket: "Create\nRequest Ticket"
  identify: "Identify\nDate Range"
  estimate: "Estimate\nVolume & Cost"
  approve: "Approval\n(if > 500 USD)"
}

Restore: {
  label: "2. Restore Phase"
  direction: right

  check: "Check Storage\nClass"
  initiate: "Initiate\nGlacier Restore"
  monitor: "Monitor\nRestoration"
  notify: "SNS\nNotification"
}

Access: {
  label: "3. Access Phase"
  direction: right

  query: "Query via\nAthena"
  replicate: "Replicate to\nOn-Prem"
  export: "Export for\nOffline Analysis"
}

Cleanup: {
  label: "4. Cleanup Phase"
  direction: right

  document: "Document\nAccess"
  ttl: "Wait for\n7-day TTL"
  verify: "Verify\nCleanup"
}

Request.ticket -> Request.identify -> Request.estimate -> Request.approve
Request.approve -> Restore.check
Restore.check -> Restore.initiate -> Restore.monitor -> Restore.notify
Restore.notify -> Access.query
Restore.notify -> Access.replicate
Restore.notify -> Access.export
Access.query -> Cleanup.document
Access.replicate -> Cleanup.document
Access.export -> Cleanup.document
Cleanup.document -> Cleanup.ttl -> Cleanup.verify
```

## Cost Estimation

### Retrieval Cost Calculator

```python
#!/usr/bin/env python3
"""estimate_retrieval_cost.py - Estimate Glacier retrieval costs."""

from datetime import datetime, timedelta

# Cost per GB by tier and retrieval type
COSTS = {
    "GLACIER_IR": {
        "retrieval": 0.03,
        "request_per_1000": 0.01,
    },
    "GLACIER": {
        "expedited": 0.03,
        "standard": 0.01,
        "bulk": 0.0025,
        "request_per_1000": 0.05,
    },
    "DEEP_ARCHIVE": {
        "standard": 0.02,
        "bulk": 0.0025,
        "request_per_1000": 0.10,
    }
}

# Approximate data volume per day (adjust based on your environment)
GB_PER_DAY = 50  # 50 GB compressed Parquet per day

def estimate_cost(
    start_date: datetime,
    end_date: datetime,
    storage_class: str,
    retrieval_tier: str = "bulk"
) -> dict:
    """Estimate retrieval cost for a date range.

    Args:
        start_date: Start of date range
        end_date: End of date range
        storage_class: GLACIER_IR, GLACIER, or DEEP_ARCHIVE
        retrieval_tier: expedited, standard, or bulk

    Returns:
        Dict with cost breakdown
    """
    days = (end_date - start_date).days + 1
    total_gb = days * GB_PER_DAY
    num_objects = days * 1000  # ~1000 objects per day

    if storage_class == "GLACIER_IR":
        retrieval_cost = total_gb * COSTS["GLACIER_IR"]["retrieval"]
        request_cost = (num_objects / 1000) * COSTS["GLACIER_IR"]["request_per_1000"]
    else:
        tier_cost = COSTS[storage_class].get(retrieval_tier, COSTS[storage_class]["bulk"])
        retrieval_cost = total_gb * tier_cost
        request_cost = (num_objects / 1000) * COSTS[storage_class]["request_per_1000"]

    return {
        "date_range": f"{start_date.date()} to {end_date.date()}",
        "days": days,
        "estimated_gb": total_gb,
        "estimated_objects": num_objects,
        "storage_class": storage_class,
        "retrieval_tier": retrieval_tier,
        "retrieval_cost": round(retrieval_cost, 2),
        "request_cost": round(request_cost, 2),
        "total_cost": round(retrieval_cost + request_cost, 2),
    }

# Example usage
if __name__ == "__main__":
    # Retrieve 30 days from Deep Archive
    result = estimate_cost(
        start_date=datetime(2025, 1, 1),
        end_date=datetime(2025, 1, 31),
        storage_class="DEEP_ARCHIVE",
        retrieval_tier="bulk"
    )
    print(f"Estimated cost: ${result['total_cost']}")
    print(f"  Data volume: {result['estimated_gb']} GB")
    print(f"  Retrieval: ${result['retrieval_cost']}")
    print(f"  Requests: ${result['request_cost']}")
```

### Cost Table

| Scenario | Volume | Storage Class | Tier | Est. Cost |
|----------|--------|---------------|------|-----------|
| 1 day investigation | 50 GB | Glacier IR | - | $1.50 |
| 1 week audit | 350 GB | Glacier | Standard | $3.50 |
| 1 month compliance | 1.5 TB | Glacier | Bulk | $3.75 |
| 1 year historical | 18 TB | Deep Archive | Bulk | $45.00 |
| Full 7-year restore | 127 TB | Deep Archive | Bulk | $317.50 |

## Retrieval Procedures

### Small Retrieval (< 1 week, < $50)

```bash
# Self-service retrieval
python3 scripts/glacier_restore.py \
  --bucket cybersec-cloudtrail-iceberg \
  --start-date 2025-06-01 \
  --end-date 2025-06-07 \
  --tier standard \
  --notify $(whoami)@company.com
```

### Large Retrieval (> 1 month, > $50)

1. Create request ticket with:
   - Business justification
   - Date range
   - Cost estimate
2. Obtain manager approval
3. Schedule retrieval during off-peak hours
4. Monitor restoration progress
5. Complete analysis within 7-day window

### Full Historical Restore (7 years)

For DR or major investigation requiring all historical data:

1. **Approval**: VP-level or compliance mandate
2. **Timeline**: 48+ hours for Deep Archive
3. **Cost**: ~$300-500 for retrieval + storage
4. **Coordination**: Schedule with on-prem team for replication capacity
5. **Cleanup**: Ensure 7-day TTL, don't extend unless necessary

## Replication After Restore

### Replicate Restored Data to On-Prem

Once data is restored from Glacier:

```bash
# Trigger ad-hoc replication for restored data
replication-manager run cloudtrail-aws-to-onprem \
  --filter "event_time >= '2025-01-01' AND event_time < '2025-02-01'" \
  --priority high
```

### Monitor Replication Progress

```bash
# Watch replication status
watch -n 30 'replication-manager status cloudtrail-aws-to-onprem --verbose'
```

## Query Examples

### Athena (AWS - Verification)

```sql
-- Count events per day for restored period
SELECT
  date_trunc('day', event_time) as event_day,
  count(*) as event_count
FROM cybersec.cloudtrail_events
WHERE event_time >= timestamp '2025-01-01'
  AND event_time < timestamp '2025-02-01'
GROUP BY 1
ORDER BY 1;
```

### Impala (On-Prem - Analysis)

```sql
-- Deep analysis after replication
SELECT
  user_identity.arn as actor,
  event_source,
  event_name,
  count(*) as occurrences
FROM cybersec.cloudtrail_events
WHERE event_time >= '2025-01-01'
  AND event_time < '2025-02-01'
  AND error_code IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY occurrences DESC
LIMIT 100;
```
