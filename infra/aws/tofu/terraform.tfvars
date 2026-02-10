# Cybersec Dask cluster configuration
#
# NOTE: aws_region is NOT set here - it's auto-detected from your AWS profile
# by devenv tasks. To use a specific region, set AWS_REGION before running:
#   AWS_REGION=us-east-1 devenv tasks run aws:provision
#
environment = "dev"
project     = "cybersec-dask"

# Developer identity - set via TF_VAR_* environment variables
# These are auto-detected by devenv tasks from git config:
#   developer_prefix = "00631868"     # 8-char hash from git email
#   developer_email  = "user@example.com"
#   ssh_key_name     = "cybersec-dask-00631868"
#   aws_region       = "us-east-1"    # project default
#
# Do NOT set ssh_key_name, developer_prefix, developer_email, or aws_region here.
# They are passed via -var flags by devenv tasks to ensure consistency.

# Network
vpc_cidr = "10.100.0.0/16"

# Cluster sizing
control_plane_count         = 1
control_plane_instance_type = "t3.large"
worker_count                = 2
worker_instance_type        = "t3.xlarge"

# Storage
root_volume_size = 50
data_volume_size = 100

# Access
allowed_ssh_cidrs = ["0.0.0.0/0"]

# Base tags (Owner is set automatically from developer_email)
tags = {}
