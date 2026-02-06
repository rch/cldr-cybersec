# RKE2 System Deployment

Running RKE2 Kubernetes at the system level on a dedicated workstation.

## Why RKE2?

RKE2 (also known as "RKE Government") provides:

- FIPS 140-2 compliance
- SELinux support out of the box
- CIS Kubernetes Benchmark hardening
- Embedded etcd (no external dependencies)
- Air-gap installation support

## Installation

### Single-Node Server

```bash
# Download and install
curl -sfL https://get.rke2.io | sudo sh -

# Enable and start
sudo systemctl enable rke2-server.service
sudo systemctl start rke2-server.service

# Wait for node to be ready
sudo /var/lib/rancher/rke2/bin/kubectl \
  --kubeconfig /etc/rancher/rke2/rke2.yaml \
  wait --for=condition=Ready node --all --timeout=300s
```

### Configure kubectl

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/rke2.yaml
sudo chown $USER ~/.kube/rke2.yaml
chmod 600 ~/.kube/rke2.yaml

export KUBECONFIG=~/.kube/rke2.yaml
```

## Storage Configuration

### Longhorn (Recommended)

```bash
# Install Longhorn
helm repo add longhorn https://charts.longhorn.io
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system --create-namespace
```

### Local Path Provisioner (Simple)

```bash
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml
```

## Network Configuration

RKE2 uses Canal (Calico + Flannel) by default:

```yaml
# /etc/rancher/rke2/config.yaml
cni: canal
```

For Cilium with eBPF:

```yaml
cni: cilium
```

## Resource Allocation

For a 64GB workstation:

```yaml
# /etc/rancher/rke2/config.yaml
kubelet-arg:
  - "system-reserved=cpu=2,memory=4Gi"
  - "kube-reserved=cpu=1,memory=2Gi"
```

## Deploying Workloads

```bash
# Deploy Flink
kubectl apply -f deploy/flink/

# Deploy Dask (using tasks)
devenv tasks run k8s:deploy-dask

# Check status
kubectl get pods -A
```

## Maintenance

```bash
# Upgrade RKE2
curl -sfL https://get.rke2.io | sudo sh -
sudo systemctl restart rke2-server

# Backup etcd
sudo /var/lib/rancher/rke2/bin/etcdctl snapshot save backup.db
```

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
