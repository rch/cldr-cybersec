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

  lifecycle {
    precondition {
      condition     = var.cloudflare_account_id != ""
      error_message = "cloudflare_account_id is required when ingress_provider=cloudflare. Add to terraform.tfvars or set CLOUDFLARE_ACCOUNT_ID."
    }
  }
}

# -----------------------------------------------------------------------------
# Tunnel Ingress Configuration (routes traffic from Cloudflare edge to services)
# -----------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "cybersec" {
  count      = var.ingress_provider == "cloudflare" ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id

  config {
    # Bastion SSH (via cloudflared on bastion, installed by user-data)
    ingress_rule {
      hostname = local.ingress_domains.bastion
      service  = "ssh://localhost:22"
    }

    # Dask Dashboard
    # Soft air-gap: K8s service DNS (cloudflared inside cluster)
    # True air-gap: bastion routes to control plane NodePort
    ingress_rule {
      hostname = local.ingress_domains.dask
      service  = var.airgap_mode ? "http://${aws_instance.control_plane[0].private_ip}:30087" : "http://simple-scheduler.dask.svc.cluster.local:8787"
    }

    # JupyterHub
    ingress_rule {
      hostname = local.ingress_domains.jupyterhub
      service  = var.airgap_mode ? "http://${aws_instance.control_plane[0].private_ip}:30080" : "http://proxy-public.jupyterhub.svc.cluster.local:80"
    }

    # Kubernetes API (replaces dashboard in air-gap)
    ingress_rule {
      hostname = local.ingress_domains.k8s
      service  = var.airgap_mode ? "https://${aws_instance.control_plane[0].private_ip}:6443" : "https://kubernetes-dashboard.kubernetes-dashboard.svc.cluster.local:443"
      origin_request {
        no_tls_verify = true
      }
    }

    # Panel Visualization
    ingress_rule {
      hostname = local.ingress_domains.viz
      service  = var.airgap_mode ? "http://${aws_instance.control_plane[0].private_ip}:30506" : "http://panel-viz.panel-viz.svc.cluster.local:80"
    }

    # Catch-all (required)
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# -----------------------------------------------------------------------------
# DNS Records
# -----------------------------------------------------------------------------

# Bastion DNS - proxied CNAME through tunnel for Zero Trust SSH
# cloudflared on the bastion (installed via user-data) connects as a tunnel
# connector, routing SSH traffic through the Cloudflare edge.
# Client uses: ProxyCommand="cloudflared access ssh --hostname %h"
resource "cloudflare_record" "bastion" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.bastion
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
  ttl     = 1 # Auto (when proxied)
  comment = "Cloudflare Tunnel: Bastion SSH (${var.developer_prefix})"
}

resource "cloudflare_record" "dask" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = local.ingress_domains.dask # Full FQDN, Cloudflare normalizes to zone
  content = "${cloudflare_zero_trust_tunnel_cloudflared.cybersec[0].id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true # Required for Zero Trust Access policies
  ttl     = 1    # Auto (when proxied)
  comment = "Cloudflare Tunnel: Dask Dashboard (${var.developer_prefix})"

  lifecycle {
    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "cloudflare_zone_id is required when ingress_provider=cloudflare. Add to terraform.tfvars or set CLOUDFLARE_ZONE_ID."
    }
  }
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

  # Protect all services under one Access application
  # (migrated from deprecated self_hosted_domains to destinations)
  destinations {
    type = "public"
    uri  = local.ingress_domains.bastion
  }
  destinations {
    type = "public"
    uri  = local.ingress_domains.dask
  }
  destinations {
    type = "public"
    uri  = local.ingress_domains.jupyterhub
  }
  destinations {
    type = "public"
    uri  = local.ingress_domains.k8s
  }
  destinations {
    type = "public"
    uri  = local.ingress_domains.viz
  }

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
# Service Token (for non-interactive SSH via cloudflared access)
# -----------------------------------------------------------------------------
# Used by Ansible and automated tools to authenticate without a browser.
# Passed via CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET env vars.

resource "cloudflare_zero_trust_access_service_token" "deploy" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  account_id = var.cloudflare_account_id
  name       = "cybersec-deploy-${var.developer_prefix}"
}

# -----------------------------------------------------------------------------
# Zero Trust Access Policies
# -----------------------------------------------------------------------------
# Policy 1 (precedence 1): Service token - allows automated SSH without browser
# Policy 2 (precedence 2): WARP device posture - interactive users via browser

resource "cloudflare_zero_trust_access_policy" "service_token" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.cybersec[0].id
  name           = "Service Token (deploy automation)"
  precedence     = 1
  decision       = "non_identity"

  include {
    service_token = [cloudflare_zero_trust_access_service_token.deploy[0].id]
  }
}

resource "cloudflare_zero_trust_access_policy" "main" {
  count = var.ingress_provider == "cloudflare" ? 1 : 0

  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.cybersec[0].id
  name           = var.cloudflare_access_open ? "Public Access (INSECURE)" : "Require WARP"
  precedence     = 2
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

output "cf_access_client_id" {
  description = "Cloudflare Access service token client ID (for automated SSH)"
  value       = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_access_service_token.deploy[0].client_id : ""
  sensitive   = true
}

output "cf_access_client_secret" {
  description = "Cloudflare Access service token client secret (for automated SSH)"
  value       = var.ingress_provider == "cloudflare" ? cloudflare_zero_trust_access_service_token.deploy[0].client_secret : ""
  sensitive   = true
}
