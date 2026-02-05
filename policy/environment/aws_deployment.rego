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
# ngrok Credential Validation (Required for AWS deployments)
# ==========================================================================

# Deny if ngrok auth token is not set (required for all ngrok functionality)
deny contains msg if {
    k8s.enabled
    not ngrok.auth_token_set
    msg := "NGROK_AUTH_TOKEN not set. Required for AWS deployments. Get from: https://dashboard.ngrok.com/get-started/your-authtoken"
}

# Deny if ngrok API key is not set (required for ngrok operator)
deny contains msg if {
    k8s.enabled
    not ngrok.api_key_set
    msg := "NGROK_API_KEY not set. Required for ngrok operator. Get from: https://dashboard.ngrok.com/api"
}

# ==========================================================================
# Cloudflare Credential Validation (Required for custom domains)
# ==========================================================================

# Warn if Cloudflare token is not set when using custom domains
warn contains msg if {
    k8s.enabled
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
