# -----------------------------------------------------------------------------
# Cloudflare Tunnel + Zero Trust Configuration
# -----------------------------------------------------------------------------
#
# Creates Cloudflare Tunnel infrastructure for secure external access to:
# - Dask Dashboard
# - JupyterHub
# - Kubernetes Dashboard
#
# Access is restricted to WARP-authenticated devices only (Zero Trust).
#
# Resources created (when ingress_provider = "cloudflare"):
# - cloudflare_tunnel: The tunnel that cloudflared connects to
# - cloudflare_record: DNS CNAMEs pointing to the tunnel
# - cloudflare_zero_trust_access_application: Zero Trust application
# - cloudflare_zero_trust_access_policy: WARP device requirement
#
# Usage:
#   export CLOUDFLARE_API_TOKEN="..."      # Zone:DNS:Edit, Tunnel:Edit, Access:Edit
#   export CLOUDFLARE_ACCOUNT_ID="..."     # From Cloudflare dashboard
#   export CLOUDFLARE_ZONE_ID="..."        # Zone ID for base domain
#   export INGRESS_PROVIDER=cloudflare
#   tofu apply -var="ingress_provider=cloudflare" \
#              -var="cloudflare_account_id=$CLOUDFLARE_ACCOUNT_ID" \
#              -var="cloudflare_zone_id=$CLOUDFLARE_ZONE_ID"
# -----------------------------------------------------------------------------

# Cloudflare provider configuration
# Uses CLOUDFLARE_API_TOKEN environment variable for authentication
provider "cloudflare" {
  # API token is read from CLOUDFLARE_API_TOKEN env var
}

# -----------------------------------------------------------------------------
# Tunnel Secret
# -----------------------------------------------------------------------------

resource "random_id" "tunnel_secret" {
  count       = var.ingress_provider == "cloudflare" ? 1 : 0
  byte_length = 32
}

# -----------------------------------------------------------------------------
# Cloudflare Tunnel (Zero Trust cloudflared tunnel)
# -----------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared" "cybersec" {
  count      = var.ingress_provider == "cloudflare" ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = "cybersec-dask-${var.developer_prefix}"
  secret     = random_id.tunnel_secret[0].b64_std
}

# -----------------------------------------------------------------------------
# Tunnel Ingress Configuration (routes traffic from Cloudflare edge to services)
# -----------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "cybersec" {
  count      = var.ingress_provider == "cloudflare" ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id

  config {
    # Dask Dashboard
    ingress_rule {
      hostname = local.ingress_domains.dask
      service  = "http://simple-scheduler.dask.svc.cluster.local:8787"
    }

    # JupyterHub
    ingress_rule {
      hostname = local.ingress_domains.jupyterhub
      service  = "http://proxy-public.jupyterhub.svc.cluster.local:80"
    }

    # Kubernetes Dashboard
    ingress_rule {
      hostname = local.ingress_domains.k8s
      service  = "https://kubernetes-dashboard.kubernetes-dashboard.svc.cluster.local:443"
      origin_request {
        no_tls_verify = true
      }
    }

    # Panel Visualization
    ingress_rule {
      hostname = local.ingress_domains.viz
      service  = "http://panel-viz.panel-viz.svc.cluster.local:80"
    }

    # Catch-all (required)
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# -----------------------------------------------------------------------------
# DNS CNAME Records
# -----------------------------------------------------------------------------

resource "cloudflare_record" "dask" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.dask # Full FQDN, Cloudflare normalizes to zone
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true # Required for Zero Trust Access policies
  ttl     = 1    # Auto (when proxied)
  comment = "Cloudflare Tunnel: Dask Dashboard (${var.developer_prefix})"
}

resource "cloudflare_record" "jupyterhub" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.jupyterhub
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
  ttl     = 1
  comment = "Cloudflare Tunnel: JupyterHub (${var.developer_prefix})"
}

resource "cloudflare_record" "k8s_dashboard" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.k8s
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
  ttl     = 1
  comment = "Cloudflare Tunnel: Kubernetes Dashboard (${var.developer_prefix})"
}

resource "cloudflare_record" "viz" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.viz
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
  ttl     = 1
  comment = "Cloudflare Tunnel: Panel Visualization (${var.developer_prefix})"
}

# -----------------------------------------------------------------------------
# Zero Trust Access Application
# -----------------------------------------------------------------------------

resource "cloudflare_zero_trust_access_application" "cybersec" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  account_id       = var.cloudflare_account_id
  name             = "cybersec-dask-${var.developer_prefix}"
  domain           = local.ingress_domains.dask
  type             = "self_hosted"
  session_duration = "24h"

  # Protect all four services under one Access application
  self_hosted_domains = [
    local.ingress_domains.dask,
    local.ingress_domains.jupyterhub,
    local.ingress_domains.k8s,
    local.ingress_domains.viz,
  ]

  # Skip interstitial page for better UX
  skip_interstitial = true

  # Allow CORS for API requests (Dask scheduler, Panel viz)
  cors_headers {
    allowed_methods   = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allowed_origins   = ["https://${local.ingress_domains.dask}", "https://${local.ingress_domains.jupyterhub}", "https://${local.ingress_domains.viz}"]
    allow_credentials = true
    max_age           = 86400
  }
}

# -----------------------------------------------------------------------------
# WARP Device Posture Rule
# -----------------------------------------------------------------------------
# Uses an existing posture rule if cloudflare_warp_posture_rule_id is set,
# otherwise creates a new one (requires Zero Trust API permissions).

locals {
  # Use existing posture rule ID if provided, otherwise use the created one
  warp_posture_rule_id = var.cloudflare_warp_posture_rule_id != "" ? var.cloudflare_warp_posture_rule_id : (
    length(cloudflare_zero_trust_device_posture_rule.require_warp) > 0 ? cloudflare_zero_trust_device_posture_rule.require_warp[0].id : ""
  )
}

resource "cloudflare_zero_trust_device_posture_rule" "require_warp" {
  # Only create if no existing rule ID provided
  count = var.ingress_provider == "cloudflare" && !var.cloudflare_access_open && var.cloudflare_warp_posture_rule_id == "" ? 1 : 0

  account_id  = var.cloudflare_account_id
  name        = "Require WARP (cybersec-${var.developer_prefix})"
  type        = "warp"
  description = "Requires WARP client to be connected and enrolled"
  schedule    = "24h"
}

# -----------------------------------------------------------------------------
# Zero Trust Access Policy
# -----------------------------------------------------------------------------
# Secure by default: requires WARP client enrolled in Zero Trust org.
# Set cloudflare_access_open = true for public access (demos only).

resource "cloudflare_zero_trust_access_policy" "main" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.cybersec[0].id
  name           = var.cloudflare_access_open ? "Public Access (INSECURE)" : "Require WARP"
  precedence     = 1
  decision       = var.cloudflare_access_open ? "bypass" : "allow"

  include {
    everyone = true
  }

  # When NOT open, require WARP device posture (org enrollment)
  dynamic "require" {
    for_each = !var.cloudflare_access_open && local.warp_posture_rule_id != "" ? [1] : []
    content {
      device_posture = [local.warp_posture_rule_id]
    }
  }
}

# -----------------------------------------------------------------------------
# Split Tunnel Configuration (Manual Setup Required)
# -----------------------------------------------------------------------------
# Routes traffic for managed domains through WARP so device posture checks work.
# Without this, traffic bypasses WARP and Cloudflare sees is_warp=false.
#
# IMPORTANT: Split tunnel configuration requires manual setup in the Cloudflare
# dashboard because the API requires additional permissions not typically
# available via API tokens.
#
# Manual setup steps:
# 1. Go to: Team & Resources > Devices > Device profiles > Default > Split Tunnels
# 2. Set Mode: "Include IPs and domains"
# 3. Add these domains:
#    - dask.dev.aws.zndx.org     (Dask Dashboard)
#    - jupyter.dev.aws.zndx.org  (JupyterHub)
#    - k8s.dev.aws.zndx.org      (Kubernetes Dashboard)
#    - viz.dev.aws.zndx.org      (Panel Visualization)
# 4. Keep any existing entries (e.g., 100.96.0.0/12 for CGNAT)
# 5. Wait ~10 minutes for propagation to WARP clients
#
# The resource below is commented out but shows the intended IaC configuration
# for when API permissions are available:
#
# resource "cloudflare_zero_trust_split_tunnel" "cybersec_include" {
#   count      = var.ingress_provider == "cloudflare" && !var.cloudflare_access_open ? 1 : 0
#   account_id = var.cloudflare_account_id
#   mode       = "include"
#   policy_id  = ""
#
#   tunnels {
#     address     = "100.96.0.0/12"
#     description = "Route CGNAT IPs through Cloudflare"
#   }
#   tunnels { host = local.ingress_domains.dask, description = "Dask Dashboard" }
#   tunnels { host = local.ingress_domains.jupyterhub, description = "JupyterHub" }
#   tunnels { host = local.ingress_domains.k8s, description = "Kubernetes Dashboard" }
#   tunnels { host = local.ingress_domains.viz, description = "Panel Visualization" }
# }

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "cloudflare_tunnel_id" {
  description = "Cloudflare Tunnel ID"
  value       = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id : ""
}

output "cloudflare_tunnel_token" {
  description = "Cloudflare Tunnel token for cloudflared deployment"
  value       = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].tunnel_token : ""
  sensitive   = true
}

output "cloudflare_tunnel_name" {
  description = "Cloudflare Tunnel name"
  value       = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].name : ""
}

output "ingress_urls" {
  description = "External URLs for services"
  value = var.ingress_provider == "cloudflare" ? {
    dask       = "https://${local.ingress_domains.dask}"
    jupyterhub = "https://${local.ingress_domains.jupyterhub}"
    k8s        = "https://${local.ingress_domains.k8s}"
    viz        = "https://${local.ingress_domains.viz}"
  } : {}
}

output "ingress_info" {
  description = "Ingress configuration summary"
  value = {
    provider    = var.ingress_provider
    base_domain = local.ingress_base_domain
    tunnel_id   = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id : null
    access_app  = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_access_application.cybersec[0].id : null
    domains     = local.ingress_domains
  }
}

output "access_mode" {
  description = "Current access mode for Cloudflare Zero Trust"
  value       = var.ingress_provider == "cloudflare" ? (var.cloudflare_access_open ? "PUBLIC (INSECURE)" : "WARP Required") : "N/A (not using Cloudflare)"
}
