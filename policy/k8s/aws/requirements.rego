# AWS K8s Target Requirements Policy
#
# Validates requirements for AWS RKE2 deployment with Dask/JupyterHub.
# This includes AWS credentials, ngrok, Cloudflare, SSH access, and developer isolation.
#
# Run with: conftest test build/environment.json --policy policy/k8s/aws/
#
# Config structure:
#   input.tools - Tool availability
#   input.aws - AWS credentials and permissions
#   input.services.ngrok - ngrok configuration
#   input.services.cloudflare - Cloudflare configuration
#   input.developer - Developer identity (prefix, email)

package k8s.aws.requirements

import rego.v1

# Access sections from environment.json
tools := input.tools
aws := input.aws
ngrok := input.services.ngrok
cloudflare := input.services.cloudflare
k8s := input.kubernetes
developer := input.developer
ingress_provider := input.ingress_provider

# ==========================================================================
# AWS Credential Validation
# ==========================================================================

# Deny if AWS credentials are not configured
deny contains msg if {
    not aws.credentials_configured
    msg := "AWS credentials not configured. Run: aws configure or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
}

# Deny if AWS S3 access is not available (required for state storage)
deny contains msg if {
    aws.credentials_configured
    not aws.permissions.s3_access
    msg := "AWS S3 access not available. Required for Terraform state and data storage."
}

# Warn if EC2 describe permission missing
warn contains msg if {
    aws.credentials_configured
    not aws.permissions.ec2_describe
    msg := "AWS EC2 describe permission not available. Instance verification may not work."
}

# ==========================================================================
# Infrastructure Tools Validation
# ==========================================================================

# Deny if no IaC tool available (tofu or terraform)
deny contains msg if {
    not tools.iac_tool
    msg := "No infrastructure tool found. Install: brew install opentofu (recommended) or brew install terraform"
}

# Deny if ansible-playbook not available
deny contains msg if {
    not tools.ansible_playbook
    msg := "ansible-playbook not found. Install: pip install ansible or brew install ansible"
}

# ==========================================================================
# ngrok Credential Validation (when ingress_provider == "ngrok")
# ==========================================================================

# Deny if ngrok auth token not set (when using ngrok)
deny contains msg if {
    ingress_provider == "ngrok"
    not ngrok.auth_token_set
    msg := "NGROK_AUTH_TOKEN not set. Required for exposing services. Get from: https://dashboard.ngrok.com/get-started/your-authtoken"
}

# Deny if ngrok API key not set (when using ngrok)
deny contains msg if {
    ingress_provider == "ngrok"
    not ngrok.api_key_set
    msg := "NGROK_API_KEY not set. Required for ngrok Kubernetes operator. Get from: https://dashboard.ngrok.com/api"
}

# ==========================================================================
# Cloudflare Validation (for ngrok custom domains)
# ==========================================================================

# Warn if Cloudflare token not set when using ngrok with custom domains
warn contains msg if {
    ingress_provider == "ngrok"
    ngrok.credentials_complete
    not cloudflare.api_token_set
    msg := "CLOUDFLARE_API_TOKEN not set. Custom domains (dask.zndx.org, etc.) will not work."
}

# ==========================================================================
# SSH Key Validation
# ==========================================================================

# Deny if SSH key doesn't exist
deny contains msg if {
    not tools.ssh_key_exists
    msg := "SSH key not found at ~/.ssh/cybersec-dask.pem. Create or copy the EC2 key pair."
}

# ==========================================================================
# Info: Show configured credentials
# ==========================================================================

# Info: AWS credentials configured
info contains msg if {
    aws.credentials_configured
    msg := sprintf("AWS credentials configured (account: %s)", [aws.account_id])
}

# Info: IaC tool available
info contains msg if {
    tools.iac_tool != null
    msg := sprintf("Infrastructure tool: %s", [tools.iac_tool])
}

# Info: ngrok credentials complete
info contains msg if {
    ngrok.credentials_complete
    msg := "ngrok credentials configured (auth_token, api_key)"
}

# Info: Cloudflare configured
info contains msg if {
    cloudflare.api_token_set
    msg := "Cloudflare API token configured for custom domains"
}

# Info: SSH key exists
info contains msg if {
    tools.ssh_key_exists
    msg := "SSH key found at ~/.ssh/cybersec-dask.pem"
}

# ==========================================================================
# Developer Isolation Validation
# ==========================================================================

# Deny if developer prefix not configured
deny contains msg if {
    not developer.prefix
    msg := "Developer prefix not configured. Run: cybersec /aws to configure developer identity"
}

# Deny if developer prefix is invalid (must be 8 hex characters)
deny contains msg if {
    developer.prefix
    not regex.match(`^[a-f0-9]{8}$`, developer.prefix)
    msg := sprintf("Developer prefix '%s' is invalid. Must be 8 lowercase hex characters.", [developer.prefix])
}

# Warn if developer email not configured
warn contains msg if {
    developer.prefix
    not developer.email
    msg := "Developer email not configured. Owner tags will use auto-detected email."
}

# Info: Developer identity configured
info contains msg if {
    developer.prefix
    developer.email
    msg := sprintf("Developer identity: %s (prefix: %s)", [developer.email, developer.prefix])
}

# Info: Developer prefix (without email)
info contains msg if {
    developer.prefix
    not developer.email
    msg := sprintf("Developer prefix: %s (email will be auto-detected)", [developer.prefix])
}
