# K3d for Testing

Lightweight Kubernetes on your laptop for testing Dask, JupyterHub, and other K8s workloads.

## When to Use K3d

Use K3d when you need:

- Dask distributed compute without a full RKE2 cluster
- JupyterHub for notebook development
- Testing Kubernetes manifests before production
- Local multi-container workflows

## Installation

K3d is provisioned via devenv tasks:

```bash
# Create k3d cluster
devenv tasks run k8s:provision

# Check status
devenv tasks run k8s:status
```

## Deployed Services

### Dask

```bash
# Deploy Dask operator + cluster
devenv tasks run k8s:deploy-dask

# Start port-forward
devenv tasks run k8s:forward

# Access dashboard
open http://localhost:8787
```

### JupyterHub

```bash
# Deploy JupyterHub
devenv tasks run k8s:deploy-jupyter

# Access (after port-forward)
open http://localhost:8000
```

## Configuration

K3d uses a local registry and maps ports:

| Service | K3d Port | Host Port |
|---------|----------|-----------|
| Dask Dashboard | 8787 | 8787 |
| Dask Scheduler | 8786 | 8786 |
| JupyterHub | 8000 | 8000 |
| K8s Dashboard | 10443 | 10443 |
| API Server | 6443 | 6550 |

## Storage

K3d uses local-path provisioner for PVCs:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-data
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
```

## Cleanup

```bash
# Destroy k3d cluster
devenv tasks run k8s:destroy
```

## Transitioning to RKE2

When moving to workstation RKE2:

1. Export manifests: `kubectl get all -o yaml > manifests.yaml`
2. Update storage classes for Longhorn/Ozone
3. Apply to RKE2: `kubectl apply -f manifests.yaml`

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
