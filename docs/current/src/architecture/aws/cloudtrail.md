# CloudTrail Ingestion

## CloudTrail Configuration

### Organization Trail (OpenTofu)

```hcl
resource "aws_cloudtrail" "org_trail" {
  name                          = "cybersec-org-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail_raw.id
  include_global_service_events = true
  is_multi_region_trail         = true
  is_organization_trail         = true
  enable_logging                = true

  # Management events (API calls)
  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  # Data events for S3 (optional, high volume)
  event_selector {
    read_write_type           = "All"
    include_management_events = false

    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::sensitive-bucket/"]
    }
  }

  # Enable CloudTrail Insights
  insight_selector {
    insight_type = "ApiCallRateInsight"
  }

  insight_selector {
    insight_type = "ApiErrorRateInsight"
  }

  tags = {
    Environment = "production"
    Project     = "cybersec"
  }
}
```

## S3 Event Notifications

### SQS Queue for Flink

```hcl
resource "aws_sqs_queue" "cloudtrail_events" {
  name                       = "cybersec-cloudtrail-events"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 1209600  # 14 days
  receive_wait_time_seconds  = 20       # Long polling

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.cloudtrail_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_s3_bucket_notification" "cloudtrail_notification" {
  bucket = aws_s3_bucket.cloudtrail_raw.id

  queue {
    queue_arn     = aws_sqs_queue.cloudtrail_events.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "AWSLogs/"
    filter_suffix = ".json.gz"
  }
}
```

## Flink Ingestion Job

### Job Configuration

```yaml
# flink-cloudtrail-job.yaml
apiVersion: flink.apache.org/v1beta1
kind: FlinkDeployment
metadata:
  name: cloudtrail-ingestion
spec:
  image: cybersec/flink-cloudtrail:1.20.1
  flinkVersion: v1_20
  flinkConfiguration:
    taskmanager.numberOfTaskSlots: "4"
    state.backend: rocksdb
    state.checkpoints.dir: s3://cybersec-flink-state/checkpoints
    execution.checkpointing.interval: "120000"
    execution.checkpointing.min-pause: "60000"
  serviceAccount: flink
  jobManager:
    resource:
      memory: "2048m"
      cpu: 1
  taskManager:
    resource:
      memory: "4096m"
      cpu: 2
    replicas: 2
  job:
    jarURI: s3://cybersec-artifacts/cloudtrail-processor-1.0.jar
    parallelism: 4
    entryClass: com.cloudera.cybersec.CloudTrailProcessor
```

### Flink Job Logic (Pseudocode)

```java
// CloudTrailProcessor.java
DataStream<CloudTrailEvent> events = env
    // Read from SQS (S3 object notifications)
    .addSource(new SqsSource(sqsQueueUrl))
    // Parse S3 notification, read actual log file
    .flatMap(new S3LogReader())
    // Parse CloudTrail JSON records
    .flatMap(new CloudTrailParser())
    // Enrich with GeoIP
    .map(new GeoIPEnricher())
    // Normalize to OCSF schema
    .map(new OCSFNormalizer());

// Write to Iceberg
FlinkSink.forRowData(events)
    .table(icebergTable)
    .tableLoader(tableLoader)
    .distributionMode(DistributionMode.HASH)
    .writeParallelism(4)
    .build();
```

## Monitoring

### CloudWatch Metrics

| Metric | Alarm Threshold |
|--------|-----------------|
| SQS ApproximateAgeOfOldestMessage | > 5 minutes |
| SQS NumberOfMessagesReceived | < 1/minute (dead trail) |
| Flink checkpoint duration | > 60 seconds |
| Flink records lag | > 10,000 |

### Dead Letter Queue

Messages that fail processing 5 times go to DLQ for manual inspection:

```hcl
resource "aws_sqs_queue" "cloudtrail_dlq" {
  name                      = "cybersec-cloudtrail-dlq"
  message_retention_seconds = 1209600  # 14 days
}
```
