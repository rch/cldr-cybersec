# Remote RKE2 on AWS

Deploying and managing RKE2 clusters on AWS for benchmarking and multi-user analytics.

## Architecture

```d2
direction: down

VPC: {
  label: "AWS VPC"

  Public: {
    label: "Public Subnet"
    nlb: "Network LB"
    bastion: "Bastion Host"
  }

  Private: {
    label: "Private Subnet"

    Control: {
      label: "Control Plane"
      cp1: "RKE2 Server 1"
      cp2: "RKE2 Server 2"
      cp3: "RKE2 Server 3"
    }

    Workers: {
      label: "Worker Nodes"
      w1: "Worker 1\n(Dask)"
      w2: "Worker 2\n(Dask)"
      w3: "Worker 3\n(Jupyter)"
    }
  }

  Storage: {
    label: "Storage"
    s3: "S3 Bucket\n(Iceberg)"
    ebs: "EBS Volumes"
  }
}

Public.nlb -> Private.Control
Private.Workers -> Storage.s3
```

## Terraform Setup

### Provider Configuration

```hcl
# main.tf
provider "aws" {
  region = "us-west-2"
}

module "rke2" {
  source = "./modules/rke2"

  cluster_name    = "cybersec-benchmark"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets

  server_count    = 3
  server_type     = "m6i.xlarge"

  worker_count    = 4
  worker_type     = "r6i.2xlarge"

  enable_gpu      = false  # Set true for GPU instances
}
```

### RKE2 Configuration

```yaml
# rke2-config.yaml
write-kubeconfig-mode: "0644"
tls-san:
  - "rke2.cybersec.example.com"
cni: cilium
disable:
  - rke2-ingress-nginx
```

## Kubeconfig Access

```bash
# Via bastion
ssh -J bastion.example.com rke2-server-1 \
  "sudo cat /etc/rancher/rke2/rke2.yaml" > ~/.kube/rke2-aws.yaml

# Update server address
sed -i 's|127.0.0.1|rke2.cybersec.example.com|' ~/.kube/rke2-aws.yaml

export KUBECONFIG=~/.kube/rke2-aws.yaml
```

## Storage Configuration

### S3 for Iceberg

```yaml
# iceberg-s3-config.yaml
apiVersion: v1
kind: Secret
metadata:
  name: iceberg-s3
  namespace: data
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "<access-key>"
  AWS_SECRET_ACCESS_KEY: "<secret-key>"
  AWS_REGION: "us-west-2"
```

### EBS for Local Storage

```yaml
# storage-class.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  iops: "3000"
  throughput: "125"
```

## Deploying Workloads

```bash
# Deploy Dask
devenv tasks run k8s:deploy-dask

# Deploy JupyterHub
devenv tasks run k8s:deploy-jupyter

# Deploy OTEL Collector for telemetry ingestion
kubectl apply -f deploy/otel-collector/
```

## Cost Management

```bash
# Scale down after hours
kubectl scale deployment dask-worker --replicas=0

# Scale up for benchmarks
kubectl scale deployment dask-worker --replicas=4
```

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
