# -----------------------------------------------------------------------------
# Security Log Ingestion Pipeline
#
# CloudTrail + VPC Flow Logs → S3 → Flink FileSource → Iceberg
#
# No SQS needed: Flink's FileSource (FLIP-27) monitors S3 prefixes
# continuously, picking up new .json.gz / .log.gz files as they land.
# All S3 access goes through the existing free S3 Gateway VPC endpoint.
#
# Gated on var.enable_security_logs (default: true).
# Cloudcraft auto-discovers: S3 bucket, CloudTrail trail, VPC Flow Log,
# CloudWatch alarms — all tagged with Pipeline for the architecture diagram.
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# S3 Bucket for Raw Security Logs
# Both CloudTrail and VPC Flow Logs write here under AWSLogs/ with distinct
# path structures that Flink FileSource watches independently:
#   AWSLogs/{account}/CloudTrail/{region}/YYYY/MM/DD/*.json.gz
#   AWSLogs/{account}/vpcflowlogs/{region}/YYYY/MM/DD/*.log.gz
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "security_logs" {
  count         = var.enable_security_logs ? 1 : 0
  bucket        = "${var.project}-${var.developer_prefix}-security-logs"
  force_destroy = true # Dev environment: allow tofu destroy to empty bucket automatically

  tags = merge(local.common_tags, {
    Name     = "${var.project}-${var.developer_prefix}-security-logs"
    Pipeline = "security-log-ingestion"
  })
}

resource "aws_s3_bucket_versioning" "security_logs" {
  count  = var.enable_security_logs ? 1 : 0
  bucket = aws_s3_bucket.security_logs[0].id
  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "security_logs" {
  count  = var.enable_security_logs ? 1 : 0
  bucket = aws_s3_bucket.security_logs[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "security_logs" {
  count  = var.enable_security_logs ? 1 : 0
  bucket = aws_s3_bucket.security_logs[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Expire raw logs after retention period — Iceberg has the processed copy
resource "aws_s3_bucket_lifecycle_configuration" "security_logs" {
  count  = var.enable_security_logs ? 1 : 0
  bucket = aws_s3_bucket.security_logs[0].id

  rule {
    id     = "expire-raw-logs"
    status = "Enabled"

    filter {} # Apply to all objects in bucket

    expiration {
      days = var.security_logs_retention_days
    }
  }
}

# Bucket policy: grant CloudTrail and VPC Flow Logs write access
resource "aws_s3_bucket_policy" "security_logs" {
  count  = var.enable_security_logs ? 1 : 0
  bucket = aws_s3_bucket.security_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudTrail: check bucket ACL
      {
        Sid    = "CloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.security_logs[0].arn
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${var.project}-trail"
          }
        }
      },
      # CloudTrail: write log files
      {
        Sid    = "CloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.security_logs[0].arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${var.project}-trail"
          }
        }
      },
      # VPC Flow Logs: write log files
      {
        Sid    = "VPCFlowLogsWrite"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.security_logs[0].arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      # VPC Flow Logs: check bucket ACL and location
      {
        Sid    = "VPCFlowLogsAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action = [
          "s3:GetBucketAcl",
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.security_logs[0].arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# CloudTrail Trail (account-level, single region for dev)
# Captures all management API calls in this region. Data events (S3 object
# access) are optional and high-volume — off by default.
# -----------------------------------------------------------------------------

resource "aws_cloudtrail" "trail" {
  count                         = var.enable_security_logs ? 1 : 0
  name                          = "${var.project}-trail"
  s3_bucket_name                = aws_s3_bucket.security_logs[0].id
  include_global_service_events = true
  is_multi_region_trail         = false # Single region for dev cost savings
  enable_logging                = true

  # Management events — all API calls in this region
  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  # S3 data events for our buckets (optional — high volume)
  dynamic "event_selector" {
    for_each = var.cloudtrail_enable_data_events ? [1] : []
    content {
      read_write_type           = "All"
      include_management_events = false

      data_resource {
        type   = "AWS::S3::Object"
        values = ["${aws_s3_bucket.cybersec.arn}/"]
      }
    }
  }

  # CloudTrail Insights — anomaly detection
  dynamic "insight_selector" {
    for_each = var.cloudtrail_insight_types
    content {
      insight_type = insight_selector.value
    }
  }

  tags = merge(local.common_tags, {
    Name     = "${var.project}-trail"
    Pipeline = "security-log-ingestion"
  })

  depends_on = [aws_s3_bucket_policy.security_logs]
}

# -----------------------------------------------------------------------------
# VPC Flow Logs → S3
# Captures all ACCEPT/REJECT traffic in the VPC. Flink FileSource monitors
# the vpcflowlogs/ prefix for new .log.gz files.
#
# Flow log format uses the extended v5 fields including pkt-src/dst-addr,
# flow-direction, and traffic-path for richer security analytics.
# -----------------------------------------------------------------------------

resource "aws_flow_log" "vpc" {
  count                = var.enable_security_logs ? 1 : 0
  vpc_id               = aws_vpc.main.id
  log_destination      = aws_s3_bucket.security_logs[0].arn
  log_destination_type = "s3"
  traffic_type         = "ALL"

  # Extended v5 fields for security analytics
  # Default fields + pkt-srcaddr pkt-dstaddr flow-direction traffic-path
  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${vpc-id} $${subnet-id} $${instance-id} $${tcp-flags} $${type} $${pkt-srcaddr} $${pkt-dstaddr} $${region} $${az-id} $${sublocation-type} $${sublocation-id} $${pkt-src-aws-service} $${pkt-dst-aws-service} $${flow-direction} $${traffic-path}"

  destination_options {
    file_format                = "plain-text"
    per_hour_partition         = true
    hive_compatible_partitions = false
  }

  tags = merge(local.common_tags, {
    Name     = "${var.project}-vpc-flow-logs"
    Pipeline = "security-log-ingestion"
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Alarm — trail health
# Alerts if CloudTrail stops delivering logs (trail disabled or S3 issue).
# Shows up in Cloudcraft for operational visibility.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "cloudtrail_delivery" {
  count               = var.enable_security_logs ? 1 : 0
  alarm_name          = "${var.project}-cloudtrail-delivery"
  alarm_description   = "No CloudTrail events delivered in 1 hour — trail may be disabled or S3 write failing"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EventCount"
  namespace           = "CloudTrailMetrics"
  period              = 3600
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  tags = merge(local.common_tags, {
    Pipeline = "security-log-ingestion"
  })
}
