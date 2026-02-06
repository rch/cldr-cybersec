# Data Flow

## End-to-End Pipeline

```d2
direction: down

Sources: {
  label: "Event Sources"
  direction: right

  ct_mgmt: "CloudTrail\nManagement Events"
  ct_data: "CloudTrail\nData Events"
  ct_insight: "CloudTrail\nInsights"
}

Ingestion: {
  label: "Ingestion Layer"
  direction: right

  s3_notif: "S3 Event\nNotifications"
  sqs: "SQS Queue"
  flink_source: "Flink S3\nSource Connector"
}

Processing: {
  label: "Processing Layer"
  direction: right

  parse: "Parse JSON"
  enrich: "Enrich\n(GeoIP, ASN)"
  normalize: "Normalize\nSchema"
  partition: "Partition by\ndate/account/region"
}

Storage: {
  label: "Storage Layer"
  direction: right

  s3_iceberg: {
    label: "S3 Iceberg\n(Hot/Warm)"
    shape: cylinder
  }
  glacier: {
    label: "Glacier\n(Cold)"
    shape: cylinder
  }
  hdfs: {
    label: "HDFS/Ozone\n(On-Prem)"
    shape: cylinder
  }
}

Sources.ct_mgmt -> Ingestion.s3_notif
Sources.ct_data -> Ingestion.s3_notif
Sources.ct_insight -> Ingestion.s3_notif

Ingestion.s3_notif -> Ingestion.sqs
Ingestion.sqs -> Ingestion.flink_source

Ingestion.flink_source -> Processing.parse
Processing.parse -> Processing.enrich
Processing.enrich -> Processing.normalize
Processing.normalize -> Processing.partition

Processing.partition -> Storage.s3_iceberg
Storage.s3_iceberg -> Storage.glacier: "90d lifecycle"
Storage.s3_iceberg -> Storage.hdfs: "Replication"
```

## Event Processing Stages

### 1. Ingestion

CloudTrail delivers logs to S3 in compressed JSON format. We use:

- **S3 Event Notifications** → SQS for reliable delivery
- **Flink S3 Source** with exactly-once semantics
- **Checkpoint to S3** for fault tolerance

### 2. Parsing & Enrichment

Each CloudTrail event is:

1. **Parsed** from nested JSON structure
2. **Enriched** with:
   - GeoIP location from source IP
   - ASN/org info for network context
   - User identity normalization
3. **Validated** against expected schema

### 3. Normalization

Events are transformed to a unified schema compatible with:

- OCSF (Open Cybersecurity Schema Framework)
- Internal security team requirements
- ML model feature expectations

### 4. Partitioning

Iceberg tables are partitioned by:

```
cloudtrail_events/
  year=2026/
    month=01/
      day=27/
        account_id=123456789012/
          region=us-east-1/
            *.parquet
```

This enables:
- Efficient time-range queries
- Account-scoped investigations
- Regional compliance reporting

## Latency Targets

| Stage | Target Latency |
|-------|----------------|
| CloudTrail → S3 | ~5 minutes (AWS SLA) |
| S3 → Flink ingestion | < 1 minute |
| Processing pipeline | < 30 seconds |
| Available in Iceberg | < 2 minutes (checkpoint interval) |
| Replicated to on-prem | < 15 minutes |
