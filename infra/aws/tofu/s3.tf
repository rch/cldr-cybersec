# -----------------------------------------------------------------------------
# S3 Bucket for Dask Data Storage
# Uses developer prefix for isolation: {project}-{developer_prefix}-data
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "cybersec" {
  bucket = local.bucket_name

  tags = merge(local.common_tags, {
    Name = local.bucket_name
  })
}

# Versioning ENABLED — this bucket holds the OTEL dataset (1.3+ TB, with object
# versions present), treated as production data, so we keep version protection.
# (Matches the live bucket state; config previously said Suspended which drifted.)
resource "aws_s3_bucket_versioning" "cybersec" {
  bucket = aws_s3_bucket.cybersec.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cybersec" {
  bucket = aws_s3_bucket.cybersec.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cybersec" {
  bucket = aws_s3_bucket.cybersec.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Create initial folder structure
resource "aws_s3_object" "otel_prefix" {
  bucket  = aws_s3_bucket.cybersec.id
  key     = "otel/"
  content = ""
}

resource "aws_s3_object" "otel_validation_prefix" {
  bucket  = aws_s3_bucket.cybersec.id
  key     = "otel-validation/"
  content = ""
}
