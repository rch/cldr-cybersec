# Storage Strategy

## Tiered Storage Architecture

```d2
direction: right

Hot: {
  label: "Hot Tier\n(0-30 days)"
  style.fill: "#ffcccc"

  s3_std: {
    label: "S3 Standard"
    shape: cylinder
  }
  use: "Active queries\nReal-time alerts"
}

Warm: {
  label: "Warm Tier\n(30-90 days)"
  style.fill: "#ffffcc"

  s3_ia: {
    label: "S3 Intelligent-Tiering"
    shape: cylinder
  }
  use: "Investigation\nCompliance"
}

Cold: {
  label: "Cold Tier\n(90 days - 7 years)"
  style.fill: "#ccccff"

  glacier_ir: {
    label: "Glacier\nInstant Retrieval"
    shape: cylinder
  }
  glacier_fr: {
    label: "Glacier\nFlexible Retrieval"
    shape: cylinder
  }
  glacier_da: {
    label: "Glacier\nDeep Archive"
    shape: cylinder
  }
  use: "Audit\nLegal hold\nHistorical analysis"
}

OnPrem: {
  label: "On-Prem Primary\n(All active data)"
  style.fill: "#ccffcc"

  hdfs: {
    label: "HDFS / Ozone"
    shape: cylinder
  }
  use: "Deep analysis\nML training\nGovernance"
}

Hot.s3_std -> Warm.s3_ia: "30 days"
Warm.s3_ia -> Cold.glacier_ir: "90 days"
Cold.glacier_ir -> Cold.glacier_fr: "1 year"
Cold.glacier_fr -> Cold.glacier_da: "3 years"

Hot.s3_std -> OnPrem.hdfs: "Replicate"
Warm.s3_ia -> OnPrem.hdfs: "Replicate" {style.stroke-dash: 5}
```

## Lifecycle Policies

### S3 Lifecycle Configuration (OpenTofu)

```hcl
resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail_iceberg" {
  bucket = aws_s3_bucket.cloudtrail_iceberg.id

  rule {
    id     = "cloudtrail-tiering"
    status = "Enabled"

    filter {
      prefix = "iceberg/warehouse/cloudtrail_events/"
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
  }
}
```

## On-Prem Storage

### Replication Strategy

All data in S3 Standard and Intelligent-Tiering is replicated to on-prem:

| AWS Tier | Replicate | On-Prem Location |
|----------|-----------|------------------|
| S3 Standard | Yes (real-time) | HDFS hot tier |
| Intelligent-Tiering | Yes (batch) | HDFS warm tier |
| Glacier IR | No | Retrieved on-demand |
| Glacier/Deep Archive | No | Retrieved on-demand |

### On-Prem Retention

As on-prem capacity grows:

1. **Phase 1** (now): Replicate 0-90 day data
2. **Phase 2** (year 1): Replicate 0-1 year data
3. **Phase 3** (year 2-3): Full replication, reduce Glacier dependency

## Cost Optimization

### AWS Storage Costs (Estimated)

| Tier | $/GB/month | 1TB/day for 1 year |
|------|------------|---------------------|
| S3 Standard | $0.023 | $8,395 |
| Intelligent-Tiering | $0.021 | $7,665 |
| Glacier IR | $0.004 | $1,460 |
| Glacier | $0.0036 | $1,314 |
| Deep Archive | $0.00099 | $361 |

### Strategy

1. **Minimize hot tier** duration (30 days vs 90)
2. **On-prem for active queries** reduces Athena costs
3. **Glacier Deep Archive** for compliance-only data
4. **Delete after 7 years** unless legal hold
