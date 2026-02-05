# AWS Deployment Environment Policy
#
# Validates that required credentials and configuration are present
# for AWS deployments with ngrok and external access.
#
# Run with: conftest test build/config.json --policy policy/environment/
#
# Config structure:
#   input.effective.services.ngrok - ngrok configuration
#   input.effective.services.cloudflare - Cloudflare configuration
#   input.effective.kubernetes - K8s configuration

package environment.aws_deployment

import rego.v1

# Use effective config (merged static + runtime)
eff := input.effective
ngrok := eff.services.ngrok
cloudflare := eff.services.cloudflare
k8s := eff.kubernetes

# ==========================================================================
# ngrok Credential Validation (Always required)
# ==========================================================================
# ngrok is required for all deployments to expose services externally.
# K8s disabled state is temporary (dependency conflict being resolved).

# Deny if ngrok auth token is not set
deny contains msg if {
    not ngrok.auth_token_set
    msg := "NGROK_AUTH_TOKEN not set. Required for deployments. Get from: https://dashboard.ngrok.com/get-started/your-authtoken"
}

# Deny if ngrok API key is not set
deny contains msg if {
    not ngrok.api_key_set
    msg := "NGROK_API_KEY not set. Required for ngrok operator. Get from: https://dashboard.ngrok.com/api"
}

# ==========================================================================
# Cloudflare Credential Validation (Required for custom domains)
# ==========================================================================

# Warn if Cloudflare token is not set when using custom domains
warn contains msg if {
    ngrok.credentials_complete
    not cloudflare.api_token_set
    msg := "CLOUDFLARE_API_TOKEN not set. Custom domains (dask.zndx.org, etc.) will not work without it."
}

# ==========================================================================
# Info: Credential Status
# ==========================================================================

# Info: ngrok credentials configured
info contains msg if {
    ngrok.credentials_complete
    msg := "ngrok credentials configured (auth_token, api_key)"
}

# Info: Cloudflare credentials configured
info contains msg if {
    cloudflare.api_token_set
    msg := "Cloudflare API token configured for custom domains"
}

# Info: Show configured domains
info contains msg if {
    ngrok.auth_token_set
    msg := sprintf("ngrok domains: dask=%s, jupyterhub=%s", [ngrok.domains.dask, ngrok.domains.jupyterhub])
}

# ==========================================================================
# AWS Credential Validation (Always required for deployments)
# ==========================================================================

# Get AWS config from effective input
aws := eff.aws

# Deny if AWS credentials are not configured
deny contains msg if {
    not aws.credentials_configured
    aws.error != null
    msg := sprintf("AWS credentials not configured: %s", [aws.error])
}

# Deny if S3 access is not available
deny contains msg if {
    aws.credentials_configured
    not aws.permissions.s3_access
    msg := "AWS S3 access not available. Check IAM permissions for s3:ListBuckets"
}

# Warn if EC2 describe permission missing (needed for deployment verification)
warn contains msg if {
    aws.credentials_configured
    not aws.permissions.ec2_describe
    msg := "AWS EC2 describe permission not available. Some verification features may not work."
}

# Info: AWS credentials configured
info contains msg if {
    aws.credentials_configured
    msg := sprintf("AWS credentials configured (account: %s)", [aws.account_id])
}

# Info: AWS IAM role/user info
info contains msg if {
    aws.credentials_configured
    aws.arn != null
    msg := sprintf("AWS identity: %s", [aws.arn])
}
