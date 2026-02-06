# Local Kubernetes Development

Deploy Dask and JupyterHub on your local machine using k3d (lightweight Kubernetes) or an existing RKE2 cluster.

## Overview

| Workflow | Platform | Cluster | Best For |
|----------|----------|---------|----------|
| [k3d Development](#workflow-1-k3d-development) | macOS, Linux | k3d (auto-provisioned) | Quick iteration, zero setup |
| [RKE2 Testing](#workflow-2-rke2-testing) | Linux | Existing RKE2 | Production-like testing |

Both workflows use the same `k8s:*` devenv tasks for application deployment.

## Prerequisites

### Required Tools

All tools are provided by `devenv shell`:

| Tool | Purpose | Provided By |
|------|---------|-------------|
| `kubectl` | Kubernetes CLI | devenv |
| `helm` | Package manager | devenv |
| `k3d` | Lightweight K8s clusters | devenv |

### Platform-Specific Requirements

**macOS (k3d workflow)**:
- Podman Desktop or Podman CLI with a running machine
- Recommended: 8 GiB+ memory allocated to Podman machine

**Linux (k3d workflow)**:
- Docker or Podman installed and running

**Linux (RKE2 workflow)**:
- RKE2 installed and running (`/etc/rancher/rke2/rke2.yaml` accessible)

---

## Workflow 1: k3d Development

k3d creates lightweight k3s clusters inside containers. This is the fastest path to a working Dask cluster.

### Quick Start

```bash
# Enter devenv shell (provides kubectl, helm, k3d)
devenv shell

# Provision k3d cluster
devenv tasks run k8s:provision

# Deploy Dask
devenv tasks run k8s:deploy-dask

# Deploy JupyterHub (optional)
devenv tasks run k8s:deploy-jupyter

# Start port-forwarding
devenv tasks run k8s:forward
```

Access services:
- **Dask Dashboard**: http://localhost:8787
- **JupyterHub**: http://localhost:8000

### Step-by-Step Details

#### 1. Provision k3d Cluster

```bash
devenv tasks run k8s:provision
```

This creates a single-node k3s cluster named `cybersec` with:
- API server on port 6550
- Kubeconfig at `.devenv/state/cybersec/kubeconfig`

Verify the cluster:

```bash
export KUBECONFIG=.devenv/state/cybersec/kubeconfig
kubectl get nodes
# NAME                    STATUS   ROLES                  AGE   VERSION
# k3d-cybersec-server-0   Ready    control-plane,master   1m    v1.28.x
```

#### 2. Deploy Dask

```bash
devenv tasks run k8s:deploy-dask
```

This installs:
- Dask Kubernetes Operator
- DaskCluster CR with 1 worker (conservative for local)

Verify Dask:

```bash
kubectl get pods -n dask
# NAME                             READY   STATUS    RESTARTS   AGE
# dask-operator-xxx                1/1     Running   0          1m
# simple-scheduler-xxx             1/1     Running   0          1m
# simple-default-worker-xxx        1/1     Running   0          1m
```

#### 3. Deploy JupyterHub (Optional)

```bash
devenv tasks run k8s:deploy-jupyter
```

JupyterHub is configured for local development:
- Dummy authenticator (any username, no password)
- Single-user pods with Dask client pre-installed

#### 4. Start Port-Forwarding

```bash
devenv tasks run k8s:forward
```

This runs `kubectl port-forward` for:
- Dask Dashboard: localhost:8787
- JupyterHub: localhost:8000 (if deployed)

### Cleanup

```bash
# Delete the k3d cluster
devenv tasks run k8s:destroy

# This removes all Kubernetes resources and the cluster itself
```

---

## Workflow 2: RKE2 Testing

Use an existing RKE2 or other Kubernetes cluster for production-like testing.

### Prerequisites

1. RKE2 (or other K8s) cluster running and accessible
2. KUBECONFIG pointing to the cluster

### Quick Start

```bash
# Set KUBECONFIG to your cluster
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml

# Or for user-local config
export KUBECONFIG=~/.kube/config

# Verify connectivity
kubectl get nodes

# Deploy Dask
devenv tasks run k8s:deploy-dask

# Deploy JupyterHub (optional)
devenv tasks run k8s:deploy-jupyter

# Start port-forwarding
devenv tasks run k8s:forward
```

### RKE2 Installation (if needed)

If RKE2 is not yet installed on your Linux workstation:

```bash
# Install RKE2 server (control plane + worker)
curl -sfL https://get.rke2.io | sudo sh -

# Enable and start
sudo systemctl enable rke2-server
sudo systemctl start rke2-server

# Make kubeconfig accessible to your user
mkdir -p ~/.kube
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
chmod 600 ~/.kube/config
```

### Notes for RKE2

- `k8s:provision` is a no-op for RKE2 (cluster already exists)
- `k8s:destroy` will NOT delete your RKE2 cluster (only k3d clusters)
- Resource limits can be higher since RKE2 workstations typically have more capacity

---

## Configuration

### Dask Resources

The default Dask configuration is conservative for local development:

```yaml
# infra/dask/dask-cluster.yaml
spec:
  worker:
    replicas: 1
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        cpu: "500m"
        memory: "512Mi"
```

Scale workers for larger workloads:

```bash
# Scale to 4 workers
kubectl -n dask patch daskcluster simple \
  -p '{"spec":{"worker":{"replicas":4}}}' --type=merge

# Increase memory limits
kubectl -n dask patch daskcluster simple \
  -p '{"spec":{"worker":{"resources":{"limits":{"memory":"2Gi"}}}}}' --type=merge
```

### JupyterHub Resources

Single-user pods are constrained for local development:

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 0.1 | 0.5 |
| Memory | 256Mi | 512Mi |

### Podman Machine Resources (macOS)

The Podman machine is the resource ceiling for k3d workloads:

```bash
# Check current settings
podman machine info

# Resize (requires stop/start)
podman machine stop
podman machine set --cpus 6 --memory 8192
podman machine start
```

Recommended:
- Memory: 8 GiB minimum (16 GiB for larger workloads)
- CPUs: 4-6

### YuniKorn Scheduler (Optional)

[Apache YuniKorn](https://yunikorn.apache.org/) provides gang scheduling for Dask. This ensures all workers start together, which can improve performance for tightly-coupled workloads.

YuniKorn is only supported for local deployments (k3d and local RKE2), not AWS.

To enable:

```bash
# Deploy YuniKorn
kubectl apply -f https://raw.githubusercontent.com/apache/yunikorn-k8shim/master/deployments/scheduler/yunikorn.yaml

# Update Dask to use YuniKorn
kubectl -n dask patch daskcluster simple \
  -p '{"spec":{"worker":{"spec":{"schedulerName":"yunikorn"}}}}' --type=merge
```

Access YuniKorn UI:

```bash
kubectl port-forward svc/yunikorn-service -n yunikorn 9889:9889
# Open http://localhost:9889
```

---

## Task Reference

All tasks are idempotent and can be run multiple times safely.

### k8s:status

Check cluster connectivity and deployed resources.

```bash
devenv tasks run k8s:status
```

Shows:
- Cluster connection status
- Node status
- Dask namespace resources
- JupyterHub namespace resources

### k8s:provision

Create a k3d cluster (k3d workflow only).

```bash
devenv tasks run k8s:provision
```

- Creates cluster `cybersec` with API on port 6550
- Generates kubeconfig at `.devenv/state/cybersec/kubeconfig`
- No-op if cluster already exists
- No-op if KUBECONFIG points to non-k3d cluster

### k8s:deploy-dask

Deploy Dask operator and cluster.

```bash
devenv tasks run k8s:deploy-dask
```

- Installs Dask Kubernetes Operator via Helm
- Creates DaskCluster CR in `dask` namespace
- Idempotent: updates existing installation

### k8s:deploy-jupyter

Deploy JupyterHub.

```bash
devenv tasks run k8s:deploy-jupyter
```

- Installs JupyterHub via Helm
- Configures dummy authenticator for local use
- Configures Dask client in single-user pods

### k8s:forward

Start port-forwarding for local access.

```bash
devenv tasks run k8s:forward
```

Forwards:
- 8787 → Dask scheduler dashboard
- 8000 → JupyterHub (if deployed)

Press Ctrl+C to stop forwarding.

### k8s:destroy

Delete k3d cluster (k3d workflow only).

```bash
devenv tasks run k8s:destroy
```

- Deletes the `cybersec` k3d cluster
- Removes all Kubernetes resources
- No-op for non-k3d clusters (safety measure)

---

## Troubleshooting

### k3d cluster won't start

**Symptom**: `k8s:provision` fails with container errors

**Solution**: Ensure Podman/Docker is running

```bash
# macOS with Podman
podman machine start

# Linux with Docker
sudo systemctl start docker
```

### Pods stuck in Pending

**Symptom**: Pods never reach Running state

**Check events**:

```bash
kubectl get events -A | grep -i insufficient
```

**Solution**: Increase Podman machine memory or reduce resource requests

```bash
# macOS: increase Podman memory
podman machine stop
podman machine set --memory 8192
podman machine start

# Or reduce Dask worker limits
kubectl -n dask patch daskcluster simple \
  -p '{"spec":{"worker":{"resources":{"limits":{"memory":"256Mi"}}}}}' --type=merge
```

### Port conflicts

**Symptom**: `k8s:forward` fails with "address already in use"

**Check what's using the port**:

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

**Solution**: Kill the conflicting process or use different ports

### kubectl can't connect

**Symptom**: "The connection to the server was refused"

**Check KUBECONFIG**:

```bash
echo $KUBECONFIG
# For k3d: should be .devenv/state/cybersec/kubeconfig
# For RKE2: should be /etc/rancher/rke2/rke2.yaml or ~/.kube/config

# Verify file exists and cluster is running
cat $KUBECONFIG | head -20
```

**For k3d**: Check if cluster is running

```bash
k3d cluster list
# If not listed, run k8s:provision again
```

**For RKE2**: Check service status

```bash
sudo systemctl status rke2-server
```

### JupyterHub authentication fails

**Symptom**: Can't log in to JupyterHub

For local development, JupyterHub uses dummy authentication:
- **Username**: Any value (e.g., `admin`)
- **Password**: Leave blank

---

## Service Ports

| Service | Port | URL | Notes |
|---------|------|-----|-------|
| Dask Dashboard | 8787 | http://localhost:8787 | Port-forwarded |
| JupyterHub | 8000 | http://localhost:8000 | Any username |
| k3d API Server | 6550 | — | k3d only |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Host Machine                                │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    Podman/Docker (for k3d)                        │   │
│  │                    or RKE2 (system service)                       │   │
│  │                                                                   │   │
│  │  ┌────────────────────────────────────────────────────────────┐  │   │
│  │  │                     Kubernetes Cluster                      │  │   │
│  │  │                                                             │  │   │
│  │  │  ┌─────────────────┐     ┌─────────────────────────────┐   │  │   │
│  │  │  │  dask namespace │     │   jupyterhub namespace      │   │  │   │
│  │  │  │                 │     │                             │   │  │   │
│  │  │  │ ┌─────────────┐ │     │ ┌─────────────────────────┐ │   │  │   │
│  │  │  │ │ dask-       │ │     │ │ hub-xxx                 │ │   │  │   │
│  │  │  │ │ operator    │ │     │ │ (JupyterHub)            │ │   │  │   │
│  │  │  │ └─────────────┘ │     │ └─────────────────────────┘ │   │  │   │
│  │  │  │                 │     │                             │   │  │   │
│  │  │  │ ┌─────────────┐ │     │ ┌─────────────────────────┐ │   │  │   │
│  │  │  │ │ simple-     │ │     │ │ jupyter-user-xxx        │ │   │  │   │
│  │  │  │ │ scheduler   │ │     │ │ (single-user pod)       │ │   │  │   │
│  │  │  │ └─────────────┘ │     │ └─────────────────────────┘ │   │  │   │
│  │  │  │                 │     │                             │   │  │   │
│  │  │  │ ┌─────────────┐ │     └─────────────────────────────┘   │  │   │
│  │  │  │ │ simple-     │ │                                       │  │   │
│  │  │  │ │ worker-xxx  │ │                                       │  │   │
│  │  │  │ └─────────────┘ │                                       │  │   │
│  │  │  │                 │                                       │  │   │
│  │  │  └─────────────────┘                                       │  │   │
│  │  │                                                             │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  │                                                                   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Port Forwards (k8s:forward):                                            │
│    localhost:8787 ──► simple-scheduler:8787 (Dask Dashboard)             │
│    localhost:8000 ──► hub:80 (JupyterHub)                                │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```
