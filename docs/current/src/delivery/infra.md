# AWS & infrastructure

Provision and configure the **same stack** Zarf deploys, on AWS or local/lab K8s. There is no Cloudera Manager path.

## Layout

| Path | Role |
|------|------|
| `infra/aws/tofu/` | OpenTofu: VPC, EC2/RKE2 nodes, S3, security groups, outputs |
| `infra/aws/ansible/` | RKE2 server/agent, Zarf stage/deploy, Dask/Jupyter/Panel playbooks |
| `infra/aws/tofu-sandbox/` | Smaller sandbox / FSM tests |
| `infra/LOCAL.md` | k3d and existing-cluster shortcuts |
| `infra/README.md` | Mode matrix (laptop / existing K8s / AWS) |

## Modes

```text
Local k3d  →  devenv tasks k8s:provision / k8s:deploy-* / k8s:forward
Existing RKE2  →  KUBECONFIG + k8s:deploy-* or Ansible
AWS  →  tofu apply (infra/aws/tofu) + Ansible (infra/aws/ansible)
```

## AWS sketch

```bash
# Keys / quotas / target region — project CLI still under transitional name:
#   cybersec "/aws"   cybersec "/aws preflight"

cd infra/aws/tofu
# configure terraform.tfvars from terraform.tfvars.example
tofu init && tofu plan && tofu apply

# Then Ansible (or devenv aws:deploy) to install RKE2 and stage Zarf
```

Post-provision, treat the control plane like air-gap: **`zarf/scripts/converge-node.sh`** for verify/apply.

## Relationship to Zarf

| Layer | Owner |
|-------|--------|
| Cloud resources (VPC, instances, buckets) | **infra/** OpenTofu |
| OS + RKE2 + package stage | **infra/** Ansible |
| App stack desired state | **zarf/** package + converge |

## Further reading

- Root [README](https://github.com/weathership/cyberphy/blob/trunk/README.md)  
- [Zarf air-gap releases](./zarf.md)  
- [Benchmarking AWS RKE2](../workloads/benchmarking/aws-rke2.md)  
