# Local k3d + Dask + JupyterHub Setup

This document describes the local Kubernetes stack for cybersec using k3d on Podman, the Dask operator/cluster, and JupyterHub. It also captures local system configuration and memory optimizations applied for stability.

## What runs locally

- **Podman machine** provides the container runtime and VM resources.
- **k3d** creates a single-node k3s cluster inside Podman.
- **Dask operator** manages Dask clusters via CRDs.
- **Dask cluster** runs a scheduler and worker(s).
- **Kubernetes Dashboard** provides a cluster UI.
- **JupyterHub** provides notebook access.

## Key files

- [devenv.nix](../devenv.nix)
- [infra/dask/dask-cluster.yaml](dask/dask-cluster.yaml)
- [build/kubeconfig-dashboard](../build/kubeconfig-dashboard)
- [.devenv/state/kubeconfig](../.devenv/state/kubeconfig)

## Local system configuration

### Podman machine resources (macOS)

The Podman machine is the resource ceiling for k3d. It must be large enough for the Dask and JupyterHub workloads.

Current target:
- **Memory**: 8 GiB
- **CPUs**: 7

If you want to raise the limit later (e.g., 16 GiB):

1) Stop processes
2) Resize the Podman machine
3) Restart the machine
4) Restart the stack

### k3d API port

The k3d API server is exposed on a fixed host port:

- **API server**: `https://127.0.0.1:6550`

This keeps `kubectl` and port-forwarding stable between restarts.

## Stack lifecycle

### Start

Use the clean restart task:

- `devenv tasks run restart:clean`

This will:
- stop existing services
- clean temp sockets and ports
- reset Postgres state
- start the stack
- run validation (non-fatal if Flink isn’t ready)

### Stop

- `devenv processes down`

## Kubernetes access

### kubeconfig

The kubeconfig is generated at:

- `.devenv/state/kubeconfig`

It is rewritten to use `127.0.0.1` for the API server. A dashboard-friendly config is also generated:

- `build/kubeconfig-dashboard`

### Kubectl

```
KUBECONFIG=.devenv/state/kubeconfig kubectl get nodes
```

## Dask

### Operator

Installed via Helm in namespace `dask-operator`.

### Cluster

Defined in [infra/dask/dask-cluster.yaml](dask/dask-cluster.yaml). Key adjustments for local memory:

- Worker replicas: **1**
- Worker requests: **CPU 100m**, **Memory 256Mi**
- Worker limits: **CPU 500m**, **Memory 512Mi**

### Dask UI

The Dask dashboard is exposed via port-forward:

- `http://127.0.0.1:8787`

The port-forward is handled by a `devenv` process.

## JupyterHub

### Helm chart

JupyterHub is installed via Helm and pinned to a version compatible with k3s v1.21:

- Chart version **2.0.0**

### UI

- `http://127.0.0.1:8000`

### Login

The default chart uses the **dummy authenticator**, so you can log in with any username (password can be anything).

### Resource tuning

Single-user pods are constrained to fit the local node:

- CPU request: **0.1**
- CPU limit: **0.5**
- Memory request: **256Mi**
- Memory limit: **512Mi**

## Kubernetes Dashboard

### UI

- `https://127.0.0.1:10443`

### Access

Use the kubeconfig at [build/kubeconfig-dashboard](../build/kubeconfig-dashboard) in browser file-picker UIs.

## Troubleshooting

### Podman socket errors

Symptoms:
- k3d fails to create cluster
- kubeconfig missing
- errors mentioning `podman-machine-default-api.sock`

Fix:
- Ensure the Podman machine is running
- Restart it if the socket is refusing connections

### Insufficient memory

Symptoms:
- events show `0/1 nodes are available: 1 Insufficient memory`
- JupyterHub or Dask pods stuck Pending

Fix:
- increase Podman machine memory
- keep Dask worker requests/limits low
- keep JupyterHub single-user limits low

### Port-forward not reachable

Use `127.0.0.1` instead of `localhost` and confirm the forward is listening:

```
lsof -nP -iTCP:8787 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:10443 -sTCP:LISTEN
```

## URLs

- Dask UI: `http://127.0.0.1:8787`
- JupyterHub: `http://127.0.0.1:8000`
- Kubernetes Dashboard: `https://127.0.0.1:10443`
