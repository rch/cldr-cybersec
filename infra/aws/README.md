# AWS Cloud Deployment

Deploy a production-grade Dask cluster with JupyterHub on RKE2 in AWS, with secure HTTPS access via Cloudflare Zero Trust (WARP device posture).

## Quick Start (Full E2E)

```bash
# Prerequisites: AWS credentials, Cloudflare API token, WARP client enrolled
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_ZONE_ID="..."

# One-command deployment
./scripts/deploy-e2e.sh

# Access (after WARP enrollment)
open https://jupyter.dev.aws.zndx.org
open https://dask.dev.aws.zndx.org
```

For step-by-step deployment or troubleshooting, see sections below.

---

## Overview

| Component | Technology | Purpose |
|-----------|------------|---------|
| Infrastructure | OpenTofu | VPC, EC2, IAM, Security Groups, S3, Cloudflare |
| Configuration | Ansible | RKE2 installation, Dask, JupyterHub, cloudflared |
| Kubernetes | RKE2 | Production-grade K8s distribution |
| Compute | Dask | Distributed Python computing |
| Notebooks | JupyterHub | Interactive notebook environment |
| External Access | Cloudflare Zero Trust | HTTPS ingress with WARP device posture (recommended) |
| External Access | ngrok | HTTPS ingress with OAuth (alternative) |

## Prerequisites

### AWS Setup

1. AWS CLI configured with credentials:
   ```bash
   aws configure
   # Or set AWS_PROFILE
   export AWS_PROFILE=your-profile
   ```

2. Required IAM permissions:
   - EC2 (instances, VPCs, security groups)
   - IAM (roles, policies)
   - S3 (buckets)

3. SSH key pair (automatically managed):
   ```bash
   # The automation handles SSH key creation automatically.
   # It creates: ~/.ssh/cybersec-dask.pem (local)
   # And in AWS: cybersec-dask-<your-prefix> (per-developer isolation)

   # To manually ensure the key pair exists:
   devenv tasks run aws:keypair:ensure
   ```

### Cloudflare Setup (recommended for external access)

1. **Zero Trust Organization**: Enable Zero Trust at https://one.dash.cloudflare.com
2. **API Token**: Create token with permissions:
   - Zone: DNS Edit
   - Account: Cloudflare Tunnel Edit
   - Account: Access: Apps and Policies Edit
   - Account: Device Posture Write (for auto-creating WARP posture rule)
3. **Split Tunnel**: Add service domains to Include list (see External Access section)
4. **WARP Client**: Install on all access devices and enroll in your organization

Note: WARP posture rule is created automatically by Terraform for repeatability.

### ngrok Setup (alternative for external access)

1. Create an ngrok account at https://ngrok.com
2. Get your auth token from the dashboard
3. Get your API key (for operator deployment)

---

## Quick Start

```bash
# 1. Set environment variables
export AWS_PROFILE=default
export NGROK_AUTH_TOKEN=<your-token>
export NGROK_API_KEY=<your-api-key>
export NGROK_ALLOWED_EMAIL=user@company.com

# 2. Provision infrastructure
devenv tasks run aws:provision

# 3. Generate Ansible inventory
devenv tasks run aws:inventory

# 4. Deploy full stack
devenv tasks run aws:deploy

# 5. Verify deployment
devenv tasks run aws:verify
```

Access:
- **JupyterHub**: https://jupyter.your-ngrok-domain.ngrok-free.app (or custom domain)
- **SSH**: `devenv tasks run aws:ssh`

---

## Detailed Workflow

### Phase 1: Provision Infrastructure

```bash
devenv tasks run aws:provision
```

This runs `tofu apply` to create:
- VPC with public and private subnets
- NAT Gateway for outbound traffic
- Bastion host in public subnet
- 3 control plane nodes (m6i.xlarge)
- 3 worker nodes (m6i.2xlarge)
- S3 bucket for data storage
- VPC endpoints for air-gapped operation

Alternatively, run OpenTofu directly:

```bash
cd infra/aws/tofu
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your SSH key name
tofu init
tofu plan
tofu apply
```

### Phase 2: Generate Inventory

```bash
devenv tasks run aws:inventory
```

Generates Ansible inventory from Tofu output:

```bash
# Or manually:
cd infra/aws/tofu
tofu output -raw ansible_inventory > ../ansible/inventory/hosts
```

### Phase 3: Deploy Applications

Deploy the full stack:

```bash
devenv tasks run aws:deploy
```

Or deploy components individually:

```bash
# RKE2 cluster (automatically included in aws:deploy)
cd infra/aws/ansible
ansible-playbook playbooks/site.yml

# Dask only
devenv tasks run aws:deploy:dask

# ngrok operator
devenv tasks run aws:deploy:ngrok

# JupyterHub
devenv tasks run aws:deploy:jupyterhub
```

### Phase 4: Verify Deployment

```bash
devenv tasks run aws:verify
```

Checks:
- All nodes are Ready
- Dask operator is running
- Dask workers are healthy
- JupyterHub is accessible
- ngrok tunnel is established

---

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `AWS_PROFILE` | AWS credentials profile | `default` |
| `NGROK_AUTH_TOKEN` | ngrok authentication token | `2abc...` |
| `NGROK_API_KEY` | ngrok API key for operator | `s_abc...` |
| `NGROK_ALLOWED_EMAIL` | Email(s) allowed via OAuth | `user@company.com` |

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token for DNS | — |
| `CLOUDFLARE_ZONE_ID` | Cloudflare zone ID | — |
| `NGROK_DOMAIN` | Custom domain for ingress | auto-generated |
| `JUPYTERHUB_ADMIN` | JupyterHub admin user | — |

### Setting Variables

```bash
# In .envrc.local (recommended, gitignored)
export AWS_PROFILE=default
export NGROK_AUTH_TOKEN=your-token
export NGROK_API_KEY=your-api-key
export NGROK_ALLOWED_EMAIL=user@company.com

# Or pass to specific tasks
NGROK_AUTH_TOKEN=xxx devenv tasks run aws:deploy:ngrok
```

---

## Task Reference

### Infrastructure Tasks

| Task | Description |
|------|-------------|
| `aws:provision` | Create VPC, EC2, IAM with OpenTofu |
| `aws:inventory` | Generate Ansible inventory from Tofu output |
| `aws:destroy` | Destroy infrastructure (keeps S3 data) |
| `aws:teardown` | Full teardown including S3 cleanup |

### Deployment Tasks

| Task | Description |
|------|-------------|
| `aws:deploy` | Deploy full stack (RKE2 + Dask + JupyterHub + ngrok) |
| `aws:deploy:dask` | Deploy Dask operator and cluster |
| `aws:deploy:jupyterhub` | Deploy JupyterHub with S3 access |
| `aws:deploy:ngrok` | Deploy ngrok operator for HTTPS ingress |
| `aws:apply` | Apply all Ansible configuration |

### Operations Tasks

| Task | Description |
|------|-------------|
| `aws:status` | Show infrastructure and service status |
| `aws:verify` | Run post-deployment verification checks |
| `aws:ssh` | SSH to bastion host |
| `aws:logs:ngrok` | View ngrok operator logs |

### S3 Tasks

| Task | Description |
|------|-------------|
| `aws:s3:clean` | Remove objects from S3 bucket |
| `aws:s3:empty` | Empty S3 bucket completely |

---

## Infrastructure Details

### VPC Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                            VPC (10.100.0.0/16)                            │
│                                                                           │
│  ┌─────────────────────────────┐   ┌─────────────────────────────────┐   │
│  │     Public Subnets          │   │      Private Subnets            │   │
│  │                             │   │                                 │   │
│  │  ┌─────────────────────┐    │   │  ┌─────────────────────────┐   │   │
│  │  │ Internet Gateway    │    │   │  │ Control Plane (x3)      │   │   │
│  │  └─────────────────────┘    │   │  │ m6i.xlarge              │   │   │
│  │           │                 │   │  │ - RKE2 server           │   │   │
│  │           ▼                 │   │  └─────────────────────────┘   │   │
│  │  ┌─────────────────────┐    │   │                                │   │
│  │  │ Bastion             │────┼───┼──────────────────────────────► │   │
│  │  │ t3.small            │    │   │  ┌─────────────────────────┐   │   │
│  │  └─────────────────────┘    │   │  │ Workers (x3)            │   │   │
│  │           │                 │   │  │ m6i.2xlarge             │   │   │
│  │           ▼                 │   │  │ - RKE2 agent            │   │   │
│  │  ┌─────────────────────┐    │   │  │ - Dask workers          │   │   │
│  │  │ NAT Gateway         │────┼───┼──► JupyterHub              │   │   │
│  │  └─────────────────────┘    │   │  └─────────────────────────┘   │   │
│  │                             │   │                                │   │
│  └─────────────────────────────┘   └─────────────────────────────────┘   │
│                                                                           │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                        VPC Endpoints                              │   │
│  │  S3 Gateway │ ECR Interface │ SSM Interface                       │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Instance Specifications

| Role | Instance Type | Count | Resources |
|------|---------------|-------|-----------|
| Bastion | t3.small | 1 | 2 vCPU, 2 GiB |
| Control Plane | m6i.xlarge | 3 | 4 vCPU, 16 GiB |
| Worker | m6i.2xlarge | 3 | 8 vCPU, 32 GiB |

### S3 Configuration

The S3 bucket stores:
- OTel telemetry data
- Iceberg table data
- JupyterHub notebooks (optional)

IAM roles provide pods with S3 access via IRSA (IAM Roles for Service Accounts).

---

## JupyterHub Integration

### Version Compatibility

Dask requires exact version matching between JupyterHub clients and workers.

The automation ensures this by:
1. Using the same Docker image (`ghcr.io/dask/dask:latest`) for both
2. Installing JupyterHub at pod startup
3. Pinning package versions in `roles/dask/files/requirements-dask.txt`

### AWS Credentials

JupyterHub pods get S3 access automatically:

1. `aws:deploy:jupyterhub` reads credentials from `AWS_PROFILE`
2. Creates Kubernetes Secret `aws-credentials` in jupyterhub namespace
3. Mounts credentials into single-user pods

### Sample Notebooks

Notebooks from `build/notebooks/` are deployed via ConfigMap:
- `Dask_S3_Validation.ipynb` - S3 out-of-core processing
- `OTel_Telemetry_Explorer.ipynb` - OTel data exploration

---

## External Access

Two ingress options are available:
- **Cloudflare Tunnel + Zero Trust** (recommended) — Secure by default, requires WARP enrollment
- **ngrok** — Simpler setup, OAuth-based access control

### Option 1: Cloudflare Tunnel + Zero Trust (Recommended)

Cloudflare Tunnel provides secure external access with **device-based authentication** via WARP client enrollment in your Zero Trust organization.

#### Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  User Device    │────▶│  Cloudflare Edge     │────▶│  K8s Cluster        │
│  (WARP Client)  │     │  (Zero Trust)        │     │  (cloudflared)      │
│                 │     │                      │     │                     │
│  ✓ Enrolled in  │     │  ✓ Device Posture    │     │  Services:          │
│    Zero Trust   │     │    Check (WARP)      │     │  - Dask Dashboard   │
│    Organization │     │  ✓ Access Policy     │     │  - JupyterHub       │
│                 │     │                      │     │  - K8s Dashboard    │
│  Traffic routed │     │  Allow if:           │     │  - Panel Viz        │
│  via Split      │     │  - WARP connected    │     │                     │
│  Tunnel Include │     │  - Posture check OK  │     │                     │
└─────────────────┘     └──────────────────────┘     └─────────────────────┘
```

#### Prerequisites

1. **Cloudflare Account** with Zero Trust enabled
2. **API Token** with permissions:
   - Zone: DNS Edit
   - Account: Cloudflare Tunnel Edit
   - Account: Access: Apps and Policies Edit
   - Account: Access: Device Posture Write (for auto-creating WARP posture rule)

3. **Split Tunnel Configuration** (add domains to Include list):
   - Go to: **Team & Resources > Devices > Device profiles > Default > Split Tunnels**
   - Mode: **Include IPs and domains**
   - Add these domains:
     - `dask.dev.aws.zndx.org`
     - `jupyter.dev.aws.zndx.org`
     - `k8s.dev.aws.zndx.org`
     - `viz.dev.aws.zndx.org`

#### Configuration

```bash
# Required environment variables
export CLOUDFLARE_API_TOKEN="your-token"

# In /tmp/cloudflare.tfvars (or add to your tfvars)
cloudflare_account_id = "your-account-id"
cloudflare_zone_id = "your-zone-id"
ingress_provider = "cloudflare"
# Note: WARP posture rule is created automatically - no manual ID needed
```

#### Tofu Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ingress_provider` | Ingress method: `ngrok` or `cloudflare` | `ngrok` |
| `cloudflare_account_id` | Cloudflare account ID | — |
| `cloudflare_zone_id` | Zone ID for base domain | — |
| `cloudflare_access_open` | `true` = public access (INSECURE) | `false` |
| `ingress_root_domain` | Root domain (e.g., `zndx.org`) | `zndx.org` |
| `ingress_env_id` | Environment ID (e.g., `aws`) | `aws` |

#### Resources Created

When `ingress_provider = "cloudflare"`:

| Resource | Purpose |
|----------|---------|
| `cloudflare_zero_trust_tunnel_cloudflared` | Tunnel connecting cluster to Cloudflare edge |
| `cloudflare_zero_trust_tunnel_cloudflared_config` | Ingress rules routing to K8s services |
| `cloudflare_record` (x4) | DNS CNAMEs for each service |
| `cloudflare_zero_trust_access_application` | Access application protecting all services |
| `cloudflare_zero_trust_device_posture_rule` | WARP posture check (auto-created) |
| `cloudflare_zero_trust_access_policy` | Policy requiring WARP device posture |

#### Service URLs

| Service | URL |
|---------|-----|
| Dask Dashboard | `https://dask.dev.aws.zndx.org` |
| JupyterHub | `https://jupyter.dev.aws.zndx.org` |
| K8s Dashboard | `https://k8s.dev.aws.zndx.org` |
| Panel Viz | `https://viz.dev.aws.zndx.org` |

#### Access Modes

**Secure (default)** — `cloudflare_access_open = false`
- Requires WARP client connected and enrolled in Zero Trust org
- Uses device posture check to verify WARP status
- Traffic must be routed through WARP (split tunnel Include list)

**Open (demos only)** — `cloudflare_access_open = true`
- Anyone can access (still requires Cloudflare login)
- ⚠️ **INSECURE** — use only for temporary demos

#### Troubleshooting Cloudflare Access

**403 Forbidden with `is_warp: false`**

Traffic isn't routing through WARP. Fix:
1. Ensure domains are in Split Tunnel **Include** list
2. Wait 10 minutes for propagation to devices
3. Reconnect WARP client

Check error details:
```bash
curl -s "https://viz.dev.aws.zndx.org/" | grep -o 'value="[^"]*"' | tail -1 | \
  sed 's/value="//' | sed 's/"$//' | \
  python3 -c "import sys,html,json; print(json.dumps(json.loads(html.unescape(sys.stdin.read())), indent=2))"
```

**Device not enrolled**

Ensure WARP client is:
1. Installed and connected
2. Enrolled in your Zero Trust organization (not just personal WARP)
3. Visible in dashboard: **Team & Resources > Devices**

---

### Option 2: ngrok

ngrok provides simpler setup with OAuth-based access control.

#### How It Works

1. **ngrok Operator** deploys as a Kubernetes controller
2. **NgrokTrafficPolicy** CRD defines OAuth requirements
3. **Ingress** routes traffic to JupyterHub service
4. Users authenticate via GitHub/Google OAuth

#### Configuration

```yaml
# ansible/group_vars/all.yml
ngrok_domain: "jupyter.yourdomain.org"  # Or use auto-generated
ngrok_allowed_email: "user@company.com"
```

#### OAuth Providers

Supported providers:
- GitHub
- Google
- OAuth 2.0 (custom)

Configure in the NgrokTrafficPolicy resource.

---

## Cost Management

### Estimated Costs

| Resource | Monthly Cost (us-east-1) |
|----------|--------------------------|
| 3x m6i.xlarge (control plane) | ~$345 |
| 3x m6i.2xlarge (workers) | ~$690 |
| 1x t3.small (bastion) | ~$15 |
| NAT Gateway | ~$32 + data |
| S3 | Variable |
| **Total (minimum)** | **~$1,100/mo** |

### Cost Reduction Tips

1. **Destroy when not in use**:
   ```bash
   devenv tasks run aws:destroy
   ```

2. **Use spot instances** for workers (modify `tofu/ec2.tf`)

3. **Reduce worker count** for testing:
   ```hcl
   # terraform.tfvars
   worker_count = 1
   ```

### Cleanup

```bash
# Destroy infrastructure (keeps S3 data)
devenv tasks run aws:destroy

# Full teardown including S3
devenv tasks run aws:teardown

# Just empty S3 bucket
devenv tasks run aws:s3:empty
```

---

## Troubleshooting

### RKE2 not starting

```bash
# SSH to control plane via bastion
devenv tasks run aws:ssh
ssh ec2-user@<control-plane-ip>

# Check RKE2 logs
journalctl -u rke2-server -f

# On workers
journalctl -u rke2-agent -f
```

### Dask workers not scheduling

```bash
# Check events
kubectl get events -n dask

# Check operator logs
kubectl logs -n dask deployment/dask-operator

# Check worker pods
kubectl describe pods -n dask -l dask.org/component=worker
```

### ngrok tunnel not working

```bash
# Check ngrok operator logs
devenv tasks run aws:logs:ngrok

# Verify ingress
kubectl get ingress -n jupyterhub

# Check NgrokTrafficPolicy
kubectl get ngroktrafficpolicy -A
```

### VPC endpoint issues

```bash
# Verify endpoints
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=<vpc-id>"

# Test from private subnet (via bastion)
aws s3 ls --endpoint-url https://s3.us-east-1.amazonaws.com
```

### JupyterHub can't access S3

```bash
# Verify secret exists
kubectl get secret aws-credentials -n jupyterhub

# Check pod environment
kubectl exec -it <jupyterhub-pod> -n jupyterhub -- env | grep AWS

# Test S3 access from pod
kubectl exec -it <jupyterhub-pod> -n jupyterhub -- aws s3 ls
```

---

## Directory Structure

```
aws/
├── README.md                    # This file
├── tofu/
│   ├── main.tf                  # Root module
│   ├── variables.tf             # Input variables (incl. Cloudflare)
│   ├── outputs.tf               # Terraform outputs
│   ├── vpc.tf                   # VPC, subnets, NAT gateway
│   ├── ec2.tf                   # EC2 instances
│   ├── iam.tf                   # IAM roles and policies
│   ├── s3.tf                    # S3 bucket
│   ├── security.tf              # Security groups
│   ├── cloudflare.tf            # Cloudflare Tunnel + Zero Trust Access
│   └── terraform.tfvars.example # Example configuration
└── ansible/
    ├── playbooks/
    │   ├── site.yml             # Main playbook (RKE2 + Dask)
    │   ├── dask-only.yml        # Dask operator only
    │   ├── jupyterhub.yml       # JupyterHub deployment
    │   └── cloudflare-tunnel.yml # cloudflared deployment
    ├── roles/
    │   ├── rke2/                # RKE2 installation
    │   ├── dask/                # Dask operator and cluster
    │   │   └── files/
    │   │       └── requirements-dask.txt
    │   ├── jupyterhub/          # JupyterHub with Dask
    │   │   ├── defaults/main.yml
    │   │   ├── tasks/main.yml
    │   │   └── templates/jupyterhub-values.yaml.j2
    │   ├── ngrok/               # ngrok operator
    │   └── cloudflare-tunnel/   # cloudflared connector
    ├── inventory/
    │   └── hosts                # Generated from tofu output
    └── group_vars/
        └── all.yml              # Cluster-wide variables
```

---

## Integration with Local Development

You can use the AWS cluster from your local devenv:

```bash
# 1. Set up SSH tunnel through bastion
ssh -L 6443:<control-plane-ip>:6443 -J ec2-user@<bastion-ip> ec2-user@<control-plane-ip>

# 2. Copy and modify kubeconfig
scp -J ec2-user@<bastion-ip> ec2-user@<control-plane-ip>:/etc/rancher/rke2/rke2.yaml ~/.kube/aws-rke2.yaml
# Edit server: https://127.0.0.1:6443

# 3. Use with k8s:* tasks
export KUBECONFIG=~/.kube/aws-rke2.yaml
devenv tasks run k8s:status
```

**Note**: For AWS deployments, prefer `aws:*` tasks over `k8s:*` tasks. The `k8s:*` tasks use Helm directly, while `aws:*` tasks use Ansible with AWS-specific configuration.

---

## Security Notes

1. **SSH Access**: Default allows 0.0.0.0/0. Restrict `allowed_ssh_cidrs` in production.

2. **Session Manager**: All nodes have SSM access for emergency access without SSH keys.

3. **Encryption**: All EBS volumes are encrypted at rest.

4. **Network Isolation**: Workers have no direct internet access (NAT for outbound only).

5. **Zero Trust Access (Cloudflare)**:
   - **Secure by default**: Requires WARP client enrolled in your Zero Trust organization
   - **Device posture**: Only devices passing WARP posture check can access services
   - **No VPN needed**: WARP client provides seamless, always-on connectivity
   - **Audit logging**: All access attempts logged in Cloudflare dashboard

6. **OAuth (ngrok)**: ngrok enforces authentication before reaching JupyterHub.

### Cloudflare Zero Trust vs ngrok

| Aspect | Cloudflare Zero Trust | ngrok |
|--------|----------------------|-------|
| Auth method | Device posture (WARP enrollment) | OAuth (email/identity) |
| Security model | Device-based (corporate-owned devices) | Identity-based (any device) |
| Setup complexity | Higher (WARP enrollment, split tunnel) | Lower (OAuth config) |
| Ongoing management | WARP client on all devices | None |
| Best for | Corporate/team environments | Quick demos, external users |

---

## Security Model

This deployment uses a **defense-in-depth** approach with multiple layers:

### Layer 1: Network Isolation
- **VPC isolation**: All cluster nodes run in private subnets
- **NAT Gateway**: Outbound-only internet access for private subnets
- **SSH via WARP only**: SSH CIDR restricted to 100.96.0.0/12 (WARP CGNAT range)
- **No NodePort exposure**: Services not directly accessible from internet

### Layer 2: Cloudflare Zero Trust
- **WARP device posture**: Only devices enrolled in your Zero Trust org can access
- **Tunnel encryption**: All traffic encrypted between Cloudflare edge and cluster
- **Access logging**: All access attempts logged in Cloudflare dashboard
- **Split tunnel routing**: Service domains routed through WARP for posture enforcement

### Layer 3: Application Authentication
- **JupyterHub**: Uses DummyAuthenticator (any password accepted)
  - **Rationale**: WARP posture check provides the security boundary
  - Username is used for session identification only
  - Users must already be on enrolled WARP device to reach login page
- **Dask Dashboard**: Read-only access, protected by WARP posture
- **Kubernetes Dashboard**: Token-based auth, protected by WARP posture

### Why DummyAuthenticator is Safe Here

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  User Device    │────▶│  Cloudflare Edge     │────▶│  K8s Cluster        │
│                 │     │                      │     │                     │
│  WARP Client    │     │  ✓ Device Posture    │     │  JupyterHub         │
│  (enrolled)     │     │    Check enforced    │     │  (DummyAuth)        │
│                 │     │                      │     │                     │
│  Traffic MUST   │     │  ✗ Non-enrolled      │     │  Only reached if    │
│  go through     │     │    devices blocked   │     │  WARP check passes  │
│  WARP tunnel    │     │                      │     │                     │
└─────────────────┘     └──────────────────────┘     └─────────────────────┘
```

**Security boundary is at Cloudflare, not JupyterHub.** Only devices that:
1. Have WARP client installed
2. Are enrolled in your Zero Trust organization
3. Pass device posture checks

...can even reach the JupyterHub login page. At that point, the user is already authenticated by device enrollment.

### When to Use Stronger Auth

Add JupyterHub OAuth/LDAP authentication when:
- Multiple untrusted users share the cluster
- You need per-user audit trails beyond device-level
- Regulatory requirements mandate application-level auth
- WARP enrollment is too broad (e.g., entire company has access)
