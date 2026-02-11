# Cloudflare Tunnel Requirements Policy
#
# Validates Cloudflare credentials and configuration when using
# Cloudflare Tunnel as the ingress provider (instead of ngrok).
#
# Run with: conftest test build/environment.json --policy policy/k8s/aws/
#
# Config structure:
#   input.services.cloudflare - Cloudflare credential status
#   input.ingress_provider - Active ingress provider ('ngrok' or 'cloudflare')

package k8s.aws.cloudflare

import rego.v1

# Access sections from environment.json
cloudflare := input.services.cloudflare
ingress_provider := input.ingress_provider

# ==========================================================================
# Cloudflare Credential Validation (when ingress_provider == "cloudflare")
# ==========================================================================

# Deny if Cloudflare API token not set when using Cloudflare ingress
deny contains msg if {
    ingress_provider == "cloudflare"
    not cloudflare.api_token_set
    msg := "CLOUDFLARE_API_TOKEN not set. Required for Cloudflare Tunnel ingress."
}

# Deny if Cloudflare account ID not set when using Cloudflare ingress
deny contains msg if {
    ingress_provider == "cloudflare"
    not cloudflare.account_id_set
    msg := "CLOUDFLARE_ACCOUNT_ID not set. Get from Cloudflare dashboard: https://dash.cloudflare.com/"
}

# Deny if Cloudflare zone ID not set when using Cloudflare ingress
deny contains msg if {
    ingress_provider == "cloudflare"
    not cloudflare.zone_id_set
    msg := "CLOUDFLARE_ZONE_ID not set. Required for DNS record creation."
}

# Deny if tunnel token not set when using Cloudflare ingress
deny contains msg if {
    ingress_provider == "cloudflare"
    not cloudflare.tunnel_token_set
    msg := "CLOUDFLARE_TUNNEL_TOKEN not set. Get from: cd infra/aws/tofu && tofu output -raw cloudflare_tunnel_token"
}

# ==========================================================================
# Ingress Provider Warnings
# ==========================================================================

# Warn about ngrok when Cloudflare credentials are complete
warn contains msg if {
    ingress_provider == "ngrok"
    cloudflare.credentials_complete
    msg := "Cloudflare credentials configured but using ngrok. Set INGRESS_PROVIDER=cloudflare for Zero Trust access."
}

# ==========================================================================
# Info Messages
# ==========================================================================

# Info: Cloudflare Tunnel ingress configured
info contains msg if {
    ingress_provider == "cloudflare"
    cloudflare.tunnel_token_set
    msg := "Cloudflare Tunnel ingress configured (Zero Trust + WARP device posture)"
}

# Info: Using ngrok ingress
info contains msg if {
    ingress_provider == "ngrok"
    msg := "Using ngrok ingress provider (OAuth-based access)"
}

# Info: Cloudflare credentials status
info contains msg if {
    cloudflare.api_token_set
    cloudflare.account_id_set
    cloudflare.zone_id_set
    msg := "Cloudflare credentials complete (API token, account ID, zone ID)"
}
