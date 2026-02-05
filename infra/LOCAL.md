# Local Kubernetes Setup

This document covers local Kubernetes configurations for the cybersec toolkit, including k3d on Podman (macOS) and RKE2 on Linux workstations.

## Overview

| Target | Platform | Provisioning | Best For |
|--------|----------|--------------|----------|
| k3d | macOS | Auto (devenv) | Development with lightweight K8s |
| RKE2 | Linux | System-managed | Enterprise features, GPU workloads |

## Quick Start

### Enable Kubernetes Stack

```bash
# Auto-detect target based on environment
ENABLE_K8S=true devenv up

# Explicit target selection
ENABLE_K8S=true CYBERSEC_K8S_TARGET=k3d devenv up    # Force k3d
ENABLE_K8S=true CYBERSEC_K8S_TARGET=rke2 devenv up   # Force RKE2
```

### Verify Configuration

```bash
# Check detected configuration
uv run python -c "
from cybersec.health.environment import gather_environment_config
import json
cfg = gather_environment_config()
print(json.dumps(cfg['kubernetes'], indent=2))
"

# Validate with conftest
conftest test build/environment.json --policy policy/environment/
```

---

## k3d on Podman

k3d creates lightweight k3s clusters inside Podman containers. This is the recommended approach for macOS development.

### Prerequisites

- Podman machine configured and running
- `k3d` CLI available (provided by devenv)

### What Gets Provisioned

When `ENABLE_K8S=true` and target is `k3d`:

1. **podman-runtime** - Ensures Podman machine is running
2. **k3d-cluster** - Creates single-node k3s cluster
3. **dask-operator** - Installs Dask Kubernetes operator
4. **dask-cluster** - Deploys Dask scheduler and workers
5. **jupyterhub** - Deploys JupyterHub for notebooks
6. **k8s-dashboard** - Deploys Kubernetes Dashboard
7. **dask-ui** - Port-forwards Dask dashboard

### Podman Machine Resources (macOS)

The Podman machine is the resource ceiling for k3d workloads.

**Recommended settings:**
- Memory: 8 GiB minimum (16 GiB for larger workloads)
- CPUs: 4-7

```bash
# Check current settings
podman machine info

# Resize (requires stop/start)
podman machine stop
podman machine set --cpus 7 --memory 8192
podman machine start
```

### k3d Configuration

| Setting | Value | Purpose |
|---------|-------|---------|
| API Port | 6550 | Fixed port for stable kubectl access |
| Cluster Name | cybersec | Matches HOCON config |
| Kubeconfig | `.devenv/state/kubeconfig` | Auto-generated |

### Dask Resource Tuning

Local Dask is configured conservatively to fit Podman constraints:

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

### JupyterHub Resource Tuning

Single-user pods are constrained for local development:

- CPU request: 0.1 / limit: 0.5
- Memory request: 256Mi / limit: 512Mi

### Troubleshooting k3d

**Podman socket errors:**
```bash
# Symptoms: k3d fails, kubeconfig missing
# Fix: Restart Podman machine
podman machine stop
podman machine start
```

**Insufficient memory:**
```bash
# Symptoms: Pods stuck Pending, "0/1 nodes available: Insufficient memory"
# Fix: Increase Podman machine memory or reduce workload limits
kubectl get events -A | grep -i memory
```

**Port conflicts:**
```bash
# Check what's using k3d API port
lsof -nP -iTCP:6550 -sTCP:LISTEN
```

---

## RKE2 on Linux

RKE2 (Rancher Kubernetes Engine 2) provides a production-grade Kubernetes distribution. Use this for Linux workstations with system-managed RKE2.

### Prerequisites

- RKE2 installed and running at system level
- Kubeconfig accessible (typically `/etc/rancher/rke2/rke2.yaml`)
- User in appropriate groups for kubeconfig access

### RKE2 Installation

If RKE2 is not yet installed:

```bash
# Install RKE2 server (control plane + worker)
curl -sfL https://get.rke2.io | sudo sh -

# Enable and start
sudo systemctl enable rke2-server
sudo systemctl start rke2-server

# Make kubeconfig accessible
sudo chmod 644 /etc/rancher/rke2/rke2.yaml
# Or copy to user location
mkdir -p ~/.kube
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
```

### Configuration

Set KUBECONFIG to point to your RKE2 cluster:

```bash
# Option 1: System kubeconfig
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml

# Option 2: User kubeconfig
export KUBECONFIG=~/.kube/config

# Then enable K8s stack
ENABLE_K8S=true devenv up
```

### What Gets Deployed

When `ENABLE_K8S=true` and target is `rke2`:

1. **dask-operator** - Installs Dask Kubernetes operator
2. **dask-cluster** - Deploys Dask scheduler and workers
3. **jupyterhub** - Deploys JupyterHub for notebooks
4. **k8s-dashboard** - Deploys Kubernetes Dashboard
5. **dask-ui** - Port-forwards Dask dashboard

Note: `podman-runtime` and `k3d-cluster` are skipped for RKE2 targets.

### Resource Configuration

RKE2 workstations typically have more resources. Adjust Dask workers accordingly:

```bash
# Scale Dask workers
kubectl -n dask patch daskcluster simple -p '{"spec":{"worker":{"replicas":4}}}' --type=merge

# Increase memory limits
kubectl -n dask patch daskcluster simple -p '{"spec":{"worker":{"resources":{"limits":{"memory":"4Gi"}}}}}' --type=merge
```

### Troubleshooting RKE2

**Permission denied on kubeconfig:**
```bash
# Check permissions
ls -la /etc/rancher/rke2/rke2.yaml

# Fix: Add user to rancher group or copy to user location
sudo usermod -aG rancher $USER
# Or
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
chmod 600 ~/.kube/config
```

**RKE2 not running:**
```bash
# Check service status
sudo systemctl status rke2-server

# View logs
sudo journalctl -u rke2-server -f
```

**API server unreachable:**
```bash
# Verify server is listening
ss -tlnp | grep 6443

# Check firewall
sudo iptables -L -n | grep 6443
```

---

## Common Configuration

### Key Files

| File | Purpose |
|------|---------|
| `devenv.nix` | Process definitions with ENABLE_K8S guards |
| `config/reference.conf` | HOCON kubernetes configuration |
| `infra/dask/dask-cluster.yaml` | Dask cluster manifest |
| `.devenv/state/kubeconfig` | Generated kubeconfig (k3d) |
| `build/kubeconfig-dashboard` | Dashboard-friendly kubeconfig |
| `policy/environment/kubernetes.rego` | Conftest validation policies |

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_K8S` | Master switch for K8s stack | `true` |
| `CYBERSEC_K8S_TARGET` | Target type (`k3d`, `rke2`, `auto`, `none`) | `rke2` |
| `KUBECONFIG` | Path to kubeconfig file | `/etc/rancher/rke2/rke2.yaml` |

### Service Ports

| Service | Port | URL |
|---------|------|-----|
| Dask Scheduler | 8786 | (internal) |
| Dask Dashboard | 8787 | http://127.0.0.1:8787 |
| JupyterHub | 8000 | http://127.0.0.1:8000 |
| K8s Dashboard | 10443 | https://127.0.0.1:10443 |
| k3d API Server | 6550 | (k3d only) |

### kubectl Access

```bash
# k3d
KUBECONFIG=.devenv/state/kubeconfig kubectl get nodes

# RKE2
KUBECONFIG=/etc/rancher/rke2/rke2.yaml kubectl get nodes

# Or set in shell profile
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
kubectl get nodes
```

---

## Policy Validation

The kubernetes.rego policy validates configuration consistency.

### Deny Rules (Failures)

| Condition | Message |
|-----------|---------|
| k3d provision + RKE2 kubeconfig | "K8s enabled with k3d provisioning but KUBECONFIG points to RKE2" |
| K8s enabled, no kubectl | "K8s enabled but no kubectl available" |

### Warn Rules (Warnings)

| Condition | Message |
|-----------|---------|
| Target=RKE2 but kubeconfig=k3d | "Target is RKE2 but KUBECONFIG points to k3d cluster" |
| KUBECONFIG file missing | "KUBECONFIG set to 'path' but file does not exist" |
| kubectl connection failed | "K8s enabled but kubectl cannot connect to cluster" |

### Running Validation

```bash
# Generate environment config
uv run python -c "
from cybersec.health.environment import gather_environment_config
import json
print(json.dumps(gather_environment_config()))
" > build/environment.json

# Run conftest
conftest test build/environment.json --policy policy/environment/

# Expected output (K8s disabled):
# 1 test, 1 passed, 0 warnings, 0 failures
```

---

## Stack Lifecycle

### Start

```bash
# Full clean restart with validation
devenv tasks run restart:clean

# Quick start
ENABLE_K8S=true devenv up
```

### Stop

```bash
# Stop all processes
devenv processes down

# Stop only K8s-related (leaves core running)
# Not directly supported - use full restart
```

### Status

```bash
# Process status
pc status

# Kubernetes resources
kubectl get pods -A
kubectl get daskcluster -n dask
```

---

## URLs Summary

| Service | URL | Notes |
|---------|-----|-------|
| Dask Dashboard | http://127.0.0.1:8787 | Port-forwarded |
| JupyterHub | http://127.0.0.1:8000 | Any username (dummy auth) |
| K8s Dashboard | https://127.0.0.1:10443 | Use kubeconfig-dashboard |
| Flink UI | http://localhost:8081 | Core stack |
| Prometheus | http://localhost:9090 | Core stack |
