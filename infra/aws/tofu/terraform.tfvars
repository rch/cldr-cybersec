# Cybersec Dask cluster configuration
aws_region  = "us-east-1"
environment = "dev"
project     = "cybersec-dask"

# SSH key for instance access
ssh_key_name = "cybersec-dask"

# Network
vpc_cidr = "10.100.0.0/16"

# Cluster sizing (smaller for initial test)
control_plane_count         = 1
control_plane_instance_type = "t3.large"
worker_count                = 2
worker_instance_type        = "t3.xlarge"

# Storage
root_volume_size = 50
data_volume_size = 100

# Access
allowed_ssh_cidrs = ["0.0.0.0/0"]

tags = {
  Owner = "cybersec-team"
}
