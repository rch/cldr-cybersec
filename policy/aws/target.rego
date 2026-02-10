# AWS Target Region Policy
#
# Validates AWS region selection and developer identity for deployment.
# This policy ensures:
# - Region is in the allowed list
# - Developer identity is configured
# - AWS credentials are available
#
# Run with: conftest test build/aws-target.json --policy policy/aws/
#
# Config structure:
#   input.target - Target region configuration
#   input.developer - Developer identity (prefix, email)
#   input.aws - AWS credentials and current region

package aws.target

import rego.v1

# Access input sections
aws := input.aws
developer := input.developer
target := input.target

# ==========================================================================
# Allowed Regions
# ==========================================================================

allowed_regions := {
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "eu-west-1",
    "eu-central-1",
    "ap-southeast-1",
}

# Deny if region not in allowed list
deny contains msg if {
    target.region
    not target.region in allowed_regions
    msg := sprintf("Region '%s' not allowed. Allowed: us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-1", [target.region])
}

# ==========================================================================
# Developer Identity Required
# ==========================================================================

deny contains msg if {
    not developer.prefix
    msg := "Developer prefix not configured. Run: cybersec /aws"
}

deny contains msg if {
    developer.prefix
    not regex.match(`^[a-f0-9]{8}$`, developer.prefix)
    msg := sprintf("Developer prefix '%s' invalid (must be 8 hex chars)", [developer.prefix])
}

# ==========================================================================
# AWS Credentials Required
# ==========================================================================

deny contains msg if {
    not aws.credentials_configured
    msg := "AWS credentials not configured. Run: aws configure"
}

# Warn if credentials may not work in target region
warn contains msg if {
    aws.credentials_configured
    aws.current_region
    target.region
    target.region != aws.current_region
    msg := sprintf("Credentials verified in %s, targeting %s - ensure cross-region access", [aws.current_region, target.region])
}

# ==========================================================================
# S3 Bucket Conflict Check
# ==========================================================================

# Warn if bucket already exists (not an error - may be intentional)
warn contains msg if {
    aws.target_bucket_exists
    developer.bucket_name
    target.region
    msg := sprintf("Bucket '%s' already exists in %s", [developer.bucket_name, target.region])
}

# ==========================================================================
# Info Messages
# ==========================================================================

info contains msg if {
    developer.prefix
    developer.email
    msg := sprintf("Developer: %s (prefix: %s)", [developer.email, developer.prefix])
}

info contains msg if {
    developer.prefix
    not developer.email
    msg := sprintf("Developer prefix: %s", [developer.prefix])
}

info contains msg if {
    target.region
    msg := sprintf("Target region: %s", [target.region])
}

info contains msg if {
    developer.bucket_name
    msg := sprintf("Bucket name: %s", [developer.bucket_name])
}

info contains msg if {
    developer.expected_tags
    msg := sprintf("Expected tags: %v", [developer.expected_tags])
}
