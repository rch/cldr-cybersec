# AWS Dask Benchmark Environment Setup Guide

**Created:** 2026-02-06 04:05 UTC
**Purpose:** Document steps to recreate the AWS Dask/RKE2 benchmark environment

## Prerequisites

1. **AWS CLI configured** with appropriate credentials
   ```bash
   aws sts get-caller-identity --profile default
   # Verify account access
   ```

2. **SSH key pair** in AWS (us-east-1)
   - Key name used: `cybersec-dask`
   - Private key location: `~/.ssh/cybersec-dask.pem`
   - To create new key:
     ```bash
     aws ec2 create-key-pair --key-name cybersec-dask --region us-east-1 \
       --query 'KeyMaterial' --output text > ~/.ssh/cybersec-dask.pem
     chmod 600 ~/.ssh/cybersec-dask.pem
     ```

3. **Tools installed:**
   - OpenTofu >= 1.6.0
   - Ansible >= 2.14
   - Python with uv

## Step 1: Deploy Infrastructure with OpenTofu

```bash
cd infra/aws/tofu

# Initialize OpenTofu
tofu init

# Configure variables (edit terraform.tfvars)
cat > terraform.tfvars << 'EOF'
aws_region  = "us-east-1"
environment = "dev"
project     = "cybersec-dask"

# SSH key for instance access
ssh_key_name = "cybersec-dask"

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

tags = {
  Owner = "cybersec-team"
}
EOF

# Plan and apply
AWS_PROFILE=default tofu plan
AWS_PROFILE=default tofu apply
```

### Infrastructure Created

| Resource | Count | Type |
|----------|-------|------|
| VPC | 1 | 10.100.0.0/16 |
| Public Subnets | 3 | For bastion/NAT |
| Private Subnets | 3 | For RKE2 nodes |
| Bastion | 1 | t3.small |
| Control Plane | 1 | t3.large |
| Workers | 2 | t3.xlarge |
| NLB | 1 | For K8s API |
| S3 Bucket | 1 | cybersec-dask-data |

## Step 2: Generate Ansible Inventory

```bash
# Generate inventory from Terraform output
AWS_PROFILE=default tofu output -raw ansible_inventory > ../ansible/inventory/hosts

# Add SSH key path to inventory
cat >> ../ansible/inventory/hosts << 'EOF'
ansible_ssh_private_key_file=~/.ssh/cybersec-dask.pem
EOF
```

### Inventory Format

```ini
[bastion]
<bastion-public-ip> ansible_user=ec2-user

[control_plane]
<cp-private-ip> ansible_user=ec2-user rke2_type=server node_name=control-plane-1

[workers]
<worker1-private-ip> ansible_user=ec2-user rke2_type=agent node_name=worker-1
<worker2-private-ip> ansible_user=ec2-user rke2_type=agent node_name=worker-2

[rke2:children]
control_plane
workers

[all:vars]
ansible_ssh_private_key_file=~/.ssh/cybersec-dask.pem
ansible_ssh_common_args='-o ProxyCommand="ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@<bastion-ip>" -o StrictHostKeyChecking=no'
k8s_api_endpoint=https://<nlb-dns>:6443
```

## Step 3: Deploy RKE2 and Dask with Ansible

```bash
cd ../ansible

# Test connectivity
ansible all -m ping --limit 'control_plane'

# Run full playbook (RKE2 + Dask + JupyterHub + ngrok)
ansible-playbook playbooks/site.yml -v
```

### Playbook Stages

1. **Common** - Install packages, set hostnames
2. **RKE2 Server** - Install Kubernetes control plane
3. **RKE2 Agent** - Join worker nodes
4. **S3 Data** - Configure S3 bucket access
5. **Dask** - Deploy Dask operator and cluster
6. **JupyterHub** - Deploy JupyterHub with Dask integration
7. **ngrok** - Set up external access with OAuth

### Environment Variables for ngrok/JupyterHub

```bash
export NGROK_AUTH_TOKEN=<your-ngrok-token>
export NGROK_API_KEY=<your-ngrok-api-key>
export CLOUDFLARE_API_TOKEN=<your-cf-token>
export CLOUDFLARE_ZONE_ID=<your-zone-id>
```

## Step 4: Scale Dask Workers

Default configuration creates 64 Dask workers which exceeds 2 EC2 worker nodes. Scale down:

```bash
# Via SSH through bastion
eval $(ssh-agent) && ssh-add ~/.ssh/cybersec-dask.pem
ssh -A -i ~/.ssh/cybersec-dask.pem ec2-user@<bastion-ip> \
  "ssh 10.100.1.88 'sudo /var/lib/rancher/rke2/bin/kubectl \
    --kubeconfig /etc/rancher/rke2/rke2.yaml \
    -n dask patch daskcluster simple \
    -p \"{\\\"spec\\\":{\\\"worker\\\":{\\\"replicas\\\":2}}}\" --type=merge'"
```

## Step 5: Verify Cluster

```bash
# Check nodes
ssh ... 'kubectl get nodes'

# Check Dask pods
ssh ... 'kubectl get pods -n dask'

# Check services
ssh ... 'kubectl get svc -n dask'
# simple-scheduler NodePort: 8786->32592, 8787->30854
```

## Step 6: Run Benchmarks

### Option A: Kubernetes Job (Recommended)

Deploy benchmark as K8s Job with matching Dask version:

```yaml
# Key settings in benchmark-job.yaml
image: ghcr.io/dask/dask:2025.2.0  # Must match scheduler version
env:
  - name: PYTHONUNBUFFERED
    value: "1"
```

```bash
kubectl apply -f build/benchmarks/benchmark-job.yaml
kubectl logs -n dask job/dask-benchmark -f
```

### Option B: Port Forward (Version mismatch issues)

```bash
# Forward scheduler port through bastion
ssh -f -N -L 8786:<worker-node-ip>:32592 \
  -i ~/.ssh/cybersec-dask.pem ec2-user@<bastion-ip>

# Run locally (requires matching dask version)
python scripts/run_benchmark.py --scheduler tcp://localhost:8786
```

## Access Endpoints

| Service | URL |
|---------|-----|
| Dask Dashboard | https://dask.zndx.org |
| JupyterHub | https://jupyter.zndx.org |
| K8s Dashboard | https://k8s.zndx.org |

## Key Files

| File | Purpose |
|------|---------|
| `infra/aws/tofu/terraform.tfvars` | Infrastructure sizing |
| `infra/aws/ansible/inventory/hosts` | Ansible inventory |
| `infra/aws/ansible/group_vars/all.yml` | Dask configuration |
| `infra/aws/ansible/roles/dask/files/requirements-dask.txt` | Pinned versions |

## Pinned Package Versions

Critical for serialization compatibility:

```
dask[complete]==2025.2.0
distributed==2025.2.0
pandas==2.2.3
numpy==2.1.3
pyarrow==18.1.0
```

## Troubleshooting

### SSH Access Issues

```bash
# Use SSH agent forwarding
eval $(ssh-agent) && ssh-add ~/.ssh/cybersec-dask.pem
ssh -A -i ~/.ssh/cybersec-dask.pem ec2-user@<bastion-ip>
```

### Dask Version Mismatch

Check scheduler version:
```bash
kubectl exec -n dask <scheduler-pod> -- python -c "import distributed; print(distributed.__version__)"
```

### Workers Pending

Check node resources:
```bash
kubectl describe nodes
kubectl get events -n dask
```

## Teardown

```bash
cd infra/aws/tofu
AWS_PROFILE=default tofu destroy
```

## Cost Estimate

| Resource | Monthly Cost (approx) |
|----------|----------------------|
| t3.small (bastion) | $15 |
| t3.large (control plane) | $60 |
| t3.xlarge × 2 (workers) | $240 |
| NAT Gateway | $32 |
| NLB | $16 |
| S3 (30GB) | $1 |
| **Total** | **~$364/month** |

Consider spot instances for workers to reduce costs by 60-70%.
