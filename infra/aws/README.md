# AWS Infrastructure for Dask on RKE2

This directory contains OpenTofu and Ansible automation for deploying a Dask cluster on RKE2 (Rancher Kubernetes Engine 2) in a soft air-gapped AWS environment.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         VPC (10.100.0.0/16)                     │
│                                                                 │
│  ┌─────────────────────┐     ┌─────────────────────────────┐   │
│  │   Public Subnets    │     │      Private Subnets        │   │
│  │                     │     │                             │   │
│  │  ┌───────────────┐  │     │  ┌───────────────────────┐  │   │
│  │  │    Bastion    │  │     │  │   RKE2 Control Plane  │  │   │
│  │  │   (t3.small)  │  │     │  │    (3x m6i.xlarge)    │  │   │
│  │  └───────────────┘  │     │  └───────────────────────┘  │   │
│  │         │           │     │            │                │   │
│  │  ┌──────┴───────┐   │     │  ┌─────────┴─────────────┐  │   │
│  │  │ NAT Gateway  │───┼─────┼──│   RKE2 Workers        │  │   │
│  │  └──────────────┘   │     │  │   (3x m6i.2xlarge)    │  │   │
│  │                     │     │  │                       │  │   │
│  └─────────────────────┘     │  │   ┌─────────────────┐ │  │   │
│            │                 │  │   │  Dask Cluster   │ │  │   │
│   ┌────────┴────────┐        │  │   │  64 workers     │ │  │   │
│   │ Internet Gateway│        │  │   └─────────────────┘ │  │   │
│   └────────┬────────┘        │  └───────────────────────┘  │   │
│            │                 │                             │   │
└────────────┼─────────────────┴─────────────────────────────┘   │
             │
     ┌───────┴────────┐
     │    Internet    │
     └────────────────┘
```

### VPC Endpoints (Soft Air-Gap)

Private subnets access AWS services via VPC endpoints:
- **S3 Gateway Endpoint** - Free, for data storage
- **ECR Interface Endpoints** - For container images
- **SSM Interface Endpoints** - For Session Manager access

## Prerequisites

- AWS CLI configured with appropriate credentials
- OpenTofu >= 1.6.0
- Ansible >= 2.14
- An SSH key pair in AWS

## Quick Start

### 1. Deploy Infrastructure

```bash
cd tofu

# Initialize OpenTofu
tofu init

# Create tfvars file
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your SSH key name

# Plan and apply
tofu plan
tofu apply
```

### 2. Generate Ansible Inventory

```bash
# Generate inventory from Terraform output
tofu output -raw ansible_inventory > ../ansible/inventory/hosts
```

### 3. Deploy RKE2 and Dask

```bash
cd ../ansible

# Run the full playbook
ansible-playbook playbooks/site.yml
```

### 4. Access the Cluster

```bash
# SSH to bastion
ssh -i ~/.ssh/your-key.pem ec2-user@$(tofu output -raw bastion_public_ip)

# From bastion, access kubectl
ssh ec2-user@<control-plane-ip>
kubectl get nodes
kubectl get pods -n dask
```

## Customization

### Cluster Sizing

Edit `terraform.tfvars`:

```hcl
# Production sizing
control_plane_count = 3
worker_count = 10
worker_instance_type = "m6i.4xlarge"
data_volume_size = 1000
```

### Dask Configuration

Edit `ansible/group_vars/all.yml`:

```yaml
dask_worker_replicas: 128
dask_worker_memory_limit: "16GB"
dask_worker_threads: 2
```

Or pass variables at runtime:

```bash
ansible-playbook playbooks/site.yml -e dask_worker_replicas=128
```

## Operations

### Scale Workers

```bash
# Scale RKE2 workers (edit tfvars then apply)
tofu apply -var worker_count=5

# Scale Dask workers
kubectl -n dask patch daskcluster simple -p '{"spec":{"worker":{"replicas":128}}}' --type=merge
```

### Access Dask Dashboard

```bash
# Port forward through bastion
ssh -L 8787:simple-scheduler.dask.svc.cluster.local:8787 \
    -J ec2-user@<bastion-ip> \
    ec2-user@<worker-ip>

# Open http://localhost:8787
```

### Destroy Infrastructure

```bash
cd tofu
tofu destroy
```

## Security Notes

1. **SSH Access**: Default allows 0.0.0.0/0. Restrict `allowed_ssh_cidrs` in production.
2. **Session Manager**: All nodes have SSM access for emergency access without SSH.
3. **Encryption**: All EBS volumes are encrypted at rest.
4. **Network**: Workers have no direct internet access (NAT gateway for egress).

## Cost Optimization

For development/testing:
- Use smaller instance types
- Reduce worker count
- Use spot instances (modify `ec2.tf`)

```hcl
# Example spot configuration for workers
resource "aws_spot_instance_request" "worker" {
  # ... existing config ...
  spot_price = "0.10"
  instance_interruption_behavior = "stop"
}
```

## Troubleshooting

### RKE2 not starting
```bash
# Check logs on control plane
journalctl -u rke2-server -f

# Check logs on worker
journalctl -u rke2-agent -f
```

### Dask workers not scheduling
```bash
# Check events
kubectl get events -n dask

# Check operator logs
kubectl logs -n dask deployment/dask-operator
```

### VPC Endpoint issues
```bash
# Verify endpoints from private subnet
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=<vpc-id>"
```
