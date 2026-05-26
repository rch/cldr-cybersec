# -----------------------------------------------------------------------------
# AWS Configuration
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

# Leaving this null falls through to the standard SDK chain
# (AWS_PROFILE -> AWS_ACCESS_KEY_ID -> SSO -> instance metadata). The aws:provision
# task pins this from $AWS_PROFILE so the resolved account matches the operator's
# active session. A hardcoded value here previously caused a deploy to land in
# the wrong account because the named profile resolved to different credentials
# than the operator's SSO session.
variable "aws_profile" {
  description = "AWS named profile to use for the provider; null = honor AWS_PROFILE env / SDK default chain"
  type        = string
  default     = null
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project" {
  description = "Project name for resource tagging"
  type        = string
  default     = "cybersec-dask"
}

# -----------------------------------------------------------------------------
# Network Configuration
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.100.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for subnets (auto-detected by devenv task)"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

# Subnet CIDRs are now computed dynamically based on AZ count
# This handles regions with 2 AZs (us-west-1) vs 3+ AZs (us-east-1)
variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (optional - computed from AZ count if empty)"
  type        = list(string)
  default     = []
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (optional - computed from AZ count if empty)"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# RKE2 Cluster Configuration
# -----------------------------------------------------------------------------

variable "rke2_version" {
  description = "RKE2 version to install"
  type        = string
  default     = "v1.34.3+rke2r1"  # Keep in sync with ansible/group_vars/all.yml
}

variable "control_plane_count" {
  description = "Number of control plane nodes"
  type        = number
  default     = 1
}

variable "control_plane_instance_type" {
  description = "Instance type for control plane nodes"
  type        = string
  default     = "m6i.xlarge"
}

variable "worker_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 8
}

variable "worker_instance_type" {
  description = "Instance type for worker nodes (r6i = memory-optimized, 4 vCPU / 32 GiB)"
  type        = string
  default     = "r6i.xlarge"
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 100
}

variable "data_volume_size" {
  description = "Data volume size in GB for workers"
  type        = number
  default     = 500
}

# -----------------------------------------------------------------------------
# Access Configuration
# -----------------------------------------------------------------------------

variable "ssh_key_name" {
  description = "Name of SSH key pair in AWS"
  type        = string
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH to bastion"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "bastion_instance_type" {
  description = "Instance type for bastion host"
  type        = string
  default     = "t3.small"
}

# -----------------------------------------------------------------------------
# Developer Isolation
# -----------------------------------------------------------------------------

variable "developer_prefix" {
  description = "Developer prefix for resource isolation (8-char hash from git email)"
  type        = string
}

variable "developer_email" {
  description = "Developer email for Owner tag (used for resource isolation)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Tags
# -----------------------------------------------------------------------------

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Air-Gap Configuration
# -----------------------------------------------------------------------------

variable "airgap_mode" {
  description = "True air-gap: remove NAT gateway and ECR endpoints; route tunnel via bastion to NodePorts"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Cloudflare Configuration (for Cloudflare Tunnel ingress)
# -----------------------------------------------------------------------------

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (from dashboard)"
  type        = string
  default     = ""
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for base domain"
  type        = string
  default     = ""
}

variable "cloudflare_access_open" {
  description = "Set to true to allow public access (INSECURE - use only for demos). Default requires WARP client enrolled in your Zero Trust org."
  type        = bool
  default     = false # Secure by default
}

variable "cloudflare_warp_posture_rule_id" {
  description = "Existing WARP device posture rule ID. If set, uses this instead of creating a new rule (useful when API token lacks Zero Trust permissions)."
  type        = string
  default     = ""
}

variable "ingress_provider" {
  description = "Ingress provider: 'ngrok' or 'cloudflare'"
  type        = string
  default     = "cloudflare"  # Cloudflare recommended for WARP device posture security

  validation {
    condition     = contains(["ngrok", "cloudflare"], var.ingress_provider)
    error_message = "ingress_provider must be 'ngrok' or 'cloudflare'"
  }
}

variable "ingress_root_domain" {
  description = "Root domain for ingress (e.g., zndx.org)"
  type        = string
  default     = "zndx.org"
}

variable "ingress_env_id" {
  description = "Environment identifier for ingress subdomains (e.g., 'aws', 'cldr')"
  type        = string
  default     = "aws"
}

locals {
  # Computed base domain: dev.{env_id}.{root_domain}
  ingress_base_domain = "dev.${var.ingress_env_id}.${var.ingress_root_domain}"
}

variable "ingress_subdomains" {
  description = "Subdomain prefixes for services"
  type = object({
    bastion    = string
    dask       = string
    jupyterhub = string
    k8s        = string
    viz        = string
  })
  default = {
    bastion    = "bastion"
    dask       = "dask"
    jupyterhub = "jupyter"
    k8s        = "k8s"
    viz        = "viz"
  }
}

locals {
  # Merge base tags with developer identity
  # The merge order ensures developer_email always overrides any Owner in var.tags
  # This guarantees consistent resource attribution for multi-developer environments
  common_tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "opentofu"
    Owner       = var.developer_email
  })

  # S3 bucket name includes developer prefix for data isolation
  # Each developer gets their own bucket: cybersec-dask-<prefix>-data
  bucket_name = "${var.project}-${var.developer_prefix}-data"

  # Compute subnet CIDRs dynamically based on AZ count
  # This handles regions with 2 AZs (us-west-1) vs 3+ AZs (us-east-1)
  az_count = length(var.availability_zones)

  # Use provided CIDRs if available, otherwise compute from VPC CIDR
  # Private subnets: 10.100.1.0/24, 10.100.2.0/24, ...
  # Public subnets:  10.100.101.0/24, 10.100.102.0/24, ...
  private_subnet_cidrs = length(var.private_subnet_cidrs) > 0 ? var.private_subnet_cidrs : [
    for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 8, i + 1)
  ]
  public_subnet_cidrs = length(var.public_subnet_cidrs) > 0 ? var.public_subnet_cidrs : [
    for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 8, i + 101)
  ]

  # Full FQDNs for ingress services
  ingress_domains = {
    bastion    = "${var.ingress_subdomains.bastion}.${local.ingress_base_domain}"
    dask       = "${var.ingress_subdomains.dask}.${local.ingress_base_domain}"
    jupyterhub = "${var.ingress_subdomains.jupyterhub}.${local.ingress_base_domain}"
    k8s        = "${var.ingress_subdomains.k8s}.${local.ingress_base_domain}"
    viz        = "${var.ingress_subdomains.viz}.${local.ingress_base_domain}"
  }
}
