# Glacier Archival

## Archival Strategy

```d2
direction: down

Active: {
  label: "Active Data (0-90 days)"

  s3_std: {
    label: "S3 Standard\n+ Intelligent-Tiering"
    shape: cylinder
  }

  queries: "Active queries\nReal-time alerts\nReplication to on-prem"
}

Archive: {
  label: "Archive Data (90 days - 7 years)"

  glacier_ir: {
    label: "Glacier Instant Retrieval\n(90 days - 1 year)"
    shape: cylinder
  }

  glacier_fr: {
    label: "Glacier Flexible Retrieval\n(1-3 years)"
    shape: cylinder
  }

  glacier_da: {
    label: "Glacier Deep Archive\n(3-7 years)"
    shape: cylinder
  }

  use: "Compliance\nAudit\nLegal hold\nHistorical analysis"
}

Retrieval: {
  label: "Retrieval Workflow"

  request: "Retrieval Request"
  restore: "Glacier Restore"
  temp_copy: "Temporary S3 Copy"
  analysis: "Query / Replicate"
  cleanup: "Auto-delete (7 days)"
}

Active.s3_std -> Archive.glacier_ir: "90 day lifecycle"
Archive.glacier_ir -> Archive.glacier_fr: "1 year"
Archive.glacier_fr -> Archive.glacier_da: "3 years"

Archive.glacier_da -> Retrieval.request: "On-demand" {style.stroke-dash: 5}
Retrieval.request -> Retrieval.restore
Retrieval.restore -> Retrieval.temp_copy
Retrieval.temp_copy -> Retrieval.analysis
Retrieval.analysis -> Retrieval.cleanup
```

## Lifecycle Configuration

### Complete Lifecycle Policy (OpenTofu)

```hcl
resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail_iceberg" {
  bucket = aws_s3_bucket.cloudtrail_iceberg.id

  # Data files lifecycle
  rule {
    id     = "iceberg-data-tiering"
    status = "Enabled"

    filter {
      prefix = "iceberg/warehouse/cloudtrail_events/data/"
    }

    transition {
      days          = 30
      storage_class = "INTELLIGENT_TIERING"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    transition {
      days          = 365
      storage_class = "GLACIER"
    }

    transition {
      days          = 1095  # 3 years
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2557  # 7 years
    }
  }

  # Metadata files - keep accessible longer
  rule {
    id     = "iceberg-metadata-tiering"
    status = "Enabled"

    filter {
      prefix = "iceberg/warehouse/cloudtrail_events/metadata/"
    }

    transition {
      days          = 90
      storage_class = "INTELLIGENT_TIERING"
    }

    transition {
      days          = 365
      storage_class = "GLACIER_IR"
    }

    # Keep metadata in Glacier IR for faster restore
    # Don't move to Deep Archive
  }

  # Clean up incomplete multipart uploads
  rule {
    id     = "cleanup-incomplete-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
```

## Retrieval Workflow

### Glacier Restore Process

```d2
direction: right

Request: {
  label: "1. Request"

  incident: "Security Incident\nor Audit Request"
  date_range: "Identify\nDate Range"
  estimate: "Estimate\nData Volume"
}

Restore: {
  label: "2. Restore"

  initiate: "Initiate\nBatch Restore"
  wait: "Wait for\nRestoration"
  notify: "SNS\nNotification"
}

Access: {
  label: "3. Access"

  query: "Query via Athena"
  replicate: "Replicate to\nOn-Prem"
  export: "Export for\nForensics"
}

Cleanup: {
  label: "4. Cleanup"

  ttl: "7-day\nExpiration"
  delete: "Auto-delete\nRestored Copy"
}

Request.incident -> Request.date_range -> Request.estimate
Request.estimate -> Restore.initiate
Restore.initiate -> Restore.wait
Restore.wait -> Restore.notify
Restore.notify -> Access.query
Restore.notify -> Access.replicate
Restore.notify -> Access.export
Access.query -> Cleanup.ttl
Access.replicate -> Cleanup.ttl
Access.export -> Cleanup.ttl
Cleanup.ttl -> Cleanup.delete
```

### Restore Script

```python
#!/usr/bin/env python3
"""glacier_restore.py - Restore CloudTrail data from Glacier."""

import boto3
from datetime import datetime, timedelta

def restore_cloudtrail_range(
    bucket: str,
    start_date: datetime,
    end_date: datetime,
    tier: str = "Bulk",  # Standard, Bulk, or Expedited
    days_available: int = 7
):
    """Restore Iceberg data files for a date range.

    Args:
        bucket: S3 bucket name
        start_date: Start of date range
        end_date: End of date range
        tier: Retrieval tier (affects speed and cost)
        days_available: Days to keep restored copy
    """
    s3 = boto3.client('s3')

    # List objects in date range partitions
    prefixes = []
    current = start_date
    while current <= end_date:
        prefix = f"iceberg/warehouse/cloudtrail_events/data/year={current.year}/month={current.month:02d}/day={current.day:02d}/"
        prefixes.append(prefix)
        current += timedelta(days=1)

    restored = []
    for prefix in prefixes:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                storage_class = obj.get('StorageClass', 'STANDARD')

                if storage_class in ['GLACIER', 'DEEP_ARCHIVE', 'GLACIER_IR']:
                    try:
                        s3.restore_object(
                            Bucket=bucket,
                            Key=key,
                            RestoreRequest={
                                'Days': days_available,
                                'GlacierJobParameters': {
                                    'Tier': tier
                                }
                            }
                        )
                        restored.append(key)
                        print(f"Initiated restore: {key}")
                    except s3.exceptions.RestoreAlreadyInProgress:
                        print(f"Already restoring: {key}")

    return restored

# Example usage:
# restore_cloudtrail_range(
#     bucket="cybersec-cloudtrail-iceberg-123456789012",
#     start_date=datetime(2025, 1, 1),
#     end_date=datetime(2025, 1, 31),
#     tier="Bulk"
# )
```

## Retrieval Times and Costs

| Tier | Retrieval Time | Cost (per GB) | Use Case |
|------|----------------|---------------|----------|
| Glacier IR | Milliseconds | $0.03 | Recent audits |
| Glacier Flexible - Expedited | 1-5 minutes | $0.03 | Urgent investigation |
| Glacier Flexible - Standard | 3-5 hours | $0.01 | Normal audit |
| Glacier Flexible - Bulk | 5-12 hours | $0.0025 | Large historical pull |
| Deep Archive - Standard | 12 hours | $0.02 | Compliance request |
| Deep Archive - Bulk | 48 hours | $0.0025 | Full historical restore |

## SNS Notifications

```hcl
resource "aws_sns_topic" "glacier_restore" {
  name = "cybersec-glacier-restore-notifications"
}

resource "aws_s3_bucket_notification" "restore_complete" {
  bucket = aws_s3_bucket.cloudtrail_iceberg.id

  topic {
    topic_arn = aws_sns_topic.glacier_restore.arn
    events    = ["s3:ObjectRestore:Completed"]
  }
}
```
