# AWS Infrastructure for Dask on RKE2

This directory contains OpenTofu and Ansible automation for deploying a Dask cluster with JupyterHub on RKE2 (Rancher Kubernetes Engine 2) in a soft air-gapped AWS environment.

## Overview

| Component | Technology | Purpose |
|-----------|------------|---------|
| Infrastructure | OpenTofu | VPC, EC2, IAM, Security Groups, S3 |
| Configuration | Ansible | RKE2 installation, Dask, JupyterHub |
| Kubernetes | RKE2 | Production-grade K8s distribution |
| Compute | Dask | Distributed Python computing |
| Notebooks | JupyterHub | Interactive notebook environment |
| External Access | ngrok | HTTPS ingress with OAuth |
| Validation | Conftest | Policy-based infrastructure checks |

## Architecture

```mermaid
flowchart TB
    subgraph internet["Internet"]
        user[User]
    end

    subgraph vpc["VPC (10.100.0.0/16)"]
        subgraph public["Public Subnets"]
            igw[Internet Gateway]
            bastion["Bastion<br/>(t3.small)"]
            nat[NAT Gateway]
        end

        subgraph private["Private Subnets"]
            subgraph cp["RKE2 Control Plane"]
                cp1["m6i.xlarge"]
                cp2["m6i.xlarge"]
                cp3["m6i.xlarge"]
            end

            subgraph workers["RKE2 Workers"]
                w1["m6i.2xlarge"]
                w2["m6i.2xlarge"]
                w3["m6i.2xlarge"]

                subgraph dask["Dask Cluster"]
                    scheduler[Scheduler]
                    dw["64 workers"]
                end
            end
        end

        subgraph endpoints["VPC Endpoints"]
            s3ep[S3 Gateway]
            ecr[ECR Interface]
            ssm[SSM Interface]
        end
    end

    user --> igw
    igw --> bastion
    bastion --> cp
    bastion --> workers
    nat --> private
    private --> endpoints
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

### 4. Deploy JupyterHub with External Access (Optional)

Use devenv tasks for streamlined deployment:

```bash
# Set required environment variables
export AWS_PROFILE=default  # AWS credentials for S3 access
export NGROK_AUTH_TOKEN=<your-token>
export NGROK_ALLOWED_EMAIL=<your-email@domain.com>

# Deploy ngrok operator (one-time)
devenv tasks run aws:deploy:ngrok

# Deploy JupyterHub
devenv tasks run aws:deploy:jupyterhub
```

JupyterHub will be accessible at the configured ngrok domain (e.g., `https://jupyter.yourdomain.org`).

### 5. Access the Cluster

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

## JupyterHub + Dask Integration

### Version Compatibility (Critical)

Dask distributed computing requires **exact version matching** between JupyterHub clients and Dask workers for serialization to work. The automation ensures this by:

1. **Same Docker Image**: JupyterHub singleuser pods use `ghcr.io/dask/dask:latest` - the exact same image as Dask workers
2. **JupyterHub Installed at Startup**: The singleuser container installs `jupyterhub` via pip at startup
3. **Pinned Package Versions**: Core packages are pinned in `roles/dask/files/requirements-dask.txt`:
   ```
   dask[complete]==2025.2.0
   distributed==2025.2.0
   pandas==2.2.3
   numpy==2.1.3
   pyarrow==18.1.0
   ```

### AWS Credentials for S3 Access

JupyterHub notebooks need AWS credentials to access S3. The deployment:

1. Reads credentials from your AWS profile (`AWS_PROFILE` env var)
2. Creates a Kubernetes Secret (`aws-credentials`) in the jupyterhub namespace
3. Injects credentials into singleuser pods via `valueFrom.secretKeyRef`

```bash
# Credentials are pulled from your AWS profile automatically
devenv tasks run aws:deploy:jupyterhub
```

### External Access via ngrok

For HTTPS access with GitHub OAuth:

1. **ngrok Operator**: Deployed to `ngrok-system` namespace
2. **Traffic Policy**: OAuth enforced via `NgrokTrafficPolicy` CRD
3. **Ingress**: Routes traffic to JupyterHub service

Configure in `ansible/group_vars/all.yml`:
```yaml
ngrok_domain: "jupyter.yourdomain.org"
ngrok_allowed_email: "user@company.com"
```

### Sample Notebooks

Notebooks from `build/notebooks/` are deployed to JupyterHub via ConfigMap:
- `Dask_S3_Validation.ipynb` - Validates out-of-core S3 processing with 10M spans
- `OTel_Telemetry_Explorer.ipynb` - Interactive OTel data exploration

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

## Policy Validation

Conftest policies ensure infrastructure consistency before and after deployment.

### Pre-Deployment Validation

```bash
# Validate Terraform plan (when infrastructure policies are defined)
tofu plan -out=plan.tfplan
tofu show -json plan.tfplan > plan.json

# Future: conftest test plan.json --policy ../../policy/infrastructure/
# Policies can check for:
# - Security group rules too permissive
# - Missing encryption on EBS volumes
# - Instance types not in approved list
```

### Runtime Validation

```bash
# Generate environment config on a cluster node
uv run python -c "
from cybersec.health.environment import gather_environment_config
import json
print(json.dumps(gather_environment_config()))
" > /tmp/environment.json

# Validate kubernetes configuration
conftest test /tmp/environment.json --policy policy/environment/
```

### Kubernetes Configuration Policies

The `policy/environment/kubernetes.rego` policy validates:

| Rule | Level | AWS Context |
|------|-------|-------------|
| Target consistency | DENY | Ensures CYBERSEC_K8S_TARGET matches actual cluster |
| kubectl connectivity | WARN | Verifies kubectl can reach RKE2 API server |
| kubeconfig existence | WARN | Confirms kubeconfig path is valid |

### Example Policy Output

```bash
# On AWS RKE2 cluster with ENABLE_K8S=true
$ conftest test build/environment.json --policy policy/environment/

PASS - build/environment.json - environment/kubernetes

# Info messages:
# - Kubernetes target: rke2
# - Using existing kubeconfig: /etc/rancher/rke2/rke2.yaml (cluster type: rke2)
```

## Devenv Tasks

Streamlined deployment via devenv:

```bash
# Show available AWS tasks
devenv tasks list | grep aws

# Deploy individual components
devenv tasks run aws:deploy:dask        # Dask operator + cluster
devenv tasks run aws:deploy:ngrok       # ngrok operator with OAuth
devenv tasks run aws:deploy:jupyterhub  # JupyterHub with notebooks

# Full stack
devenv tasks run aws:apply              # Apply all configuration
```

## Directory Structure

```
aws/
├── README.md           # This file
├── tofu/
│   ├── main.tf         # Root module
│   ├── variables.tf    # Input variables
│   ├── outputs.tf      # Terraform outputs
│   ├── vpc.tf          # VPC, subnets, NAT gateway
│   ├── ec2.tf          # EC2 instances (bastion, control plane, workers)
│   ├── iam.tf          # IAM roles and policies
│   ├── s3.tf           # S3 bucket for data storage
│   ├── security.tf     # Security groups
│   └── terraform.tfvars.example
└── ansible/
    ├── playbooks/
    │   ├── site.yml        # Main playbook (RKE2 + Dask)
    │   ├── dask-only.yml   # Dask operator only
    │   └── jupyterhub.yml  # JupyterHub deployment
    ├── roles/
    │   ├── rke2/           # RKE2 installation
    │   ├── dask/           # Dask operator and cluster
    │   │   └── files/
    │   │       └── requirements-dask.txt  # Pinned package versions
    │   ├── jupyterhub/     # JupyterHub with Dask integration
    │   │   ├── defaults/main.yml
    │   │   ├── tasks/main.yml
    │   │   └── templates/jupyterhub-values.yaml.j2
    │   └── ngrok/          # ngrok operator for external access
    ├── inventory/
    │   └── hosts           # Generated from tofu output
    └── group_vars/
        └── all.yml         # Cluster-wide variables

## Integration with Local Development

The AWS cluster can be used from your local devenv environment:

```bash
# Copy kubeconfig from bastion
scp -J ec2-user@<bastion-ip> ec2-user@<control-plane-ip>:/etc/rancher/rke2/rke2.yaml ~/.kube/aws-rke2.yaml

# Update server address to use SSH tunnel
# Edit ~/.kube/aws-rke2.yaml: server: https://127.0.0.1:6443

# Set up SSH tunnel
ssh -L 6443:<control-plane-private-ip>:6443 -J ec2-user@<bastion-ip> ec2-user@<control-plane-ip>

# In another terminal, use the cluster
export KUBECONFIG=~/.kube/aws-rke2.yaml
ENABLE_K8S=true CYBERSEC_K8S_TARGET=rke2 devenv up
```

This allows running the Dask UI port-forwards and other K8s processes against the remote AWS cluster.
