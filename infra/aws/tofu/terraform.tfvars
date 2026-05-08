# Cybersec Dask cluster configuration
#
# ISOLATION RULE — READ THIS BEFORE CHANGING aws_region
#
# AWS account 050330818249 currently hosts another cybersec-dask stack in
# us-west-1 (RKE2 + S3 bucket cybersec-dask-00631868-data) that is owned by
# a separate machine. Its tfstate is not in this checkout. Any deploy or
# destroy run from here must target us-east-1 ONLY so we cannot accidentally
# touch the us-west-1 stack.
#
# aws_region is pinned below as a guardrail. devenv tasks (aws:provision,
# aws:destroy, aws:teardown) may still export TF_VAR_aws_region when
# operating on existing tfstate from another region, but they auto-detect
# region from tfstate ARNs first — they will not silently jump regions.
environment = "dev"
project     = "cybersec-dask"
aws_region  = "us-east-1"

# Developer identity - set via TF_VAR_* environment variables
# These are auto-detected by devenv tasks from git config:
#   developer_prefix = "00631868"     # 8-char hash from git email
#   developer_email  = "user@example.com"
#   ssh_key_name     = "cybersec-dask-00631868"
#
# Do NOT set ssh_key_name, developer_prefix, or developer_email here.
# They are passed via -var flags by devenv tasks to ensure consistency.

# Network
vpc_cidr = "10.100.0.0/16"

# Cluster sizing
control_plane_count         = 1
control_plane_instance_type = "m6i.xlarge"
worker_count                = 8
worker_instance_type        = "r6i.xlarge"  # 4 vCPU, 32 GiB memory-optimized

# Storage
root_volume_size = 50
data_volume_size = 100

# Access - Cloudflare WARP CGNAT range + developer IP for debugging
# 100.96.0.0/12 = WARP enrolled devices
# 216.147.124.22/32 = developer IP (temporary, for cloudflared debugging)
allowed_ssh_cidrs = ["100.96.0.0/12", "216.147.124.22/32"]

# Cloudflare WARP posture rule (pre-created in Zero Trust dashboard)
# Required because API token lacks Zero Trust permissions to create rules
cloudflare_warp_posture_rule_id = "5a0ce53e-f932-46c9-935e-31f79b68e597"

# Air-gap mode: remove NAT gateway, restrict egress, route tunnel via bastion NodePorts
# Set to true for true air-gap deployments (pre-loaded AMIs, no internet needed)
# Set to false for Zarf-on-AWS (RKE2 installer needs internet, Zarf handles app images)
airgap_mode = false

# Base tags (Owner is set automatically from developer_email)
tags = {}
