# Dask + JupyterHub

Interactive analytics and ML workloads with distributed compute.

## Architecture

```d2
direction: right

Users: {
  label: "Users"
  u1: "Analyst 1"
  u2: "Analyst 2"
  u3: "Data Scientist"
}

JupyterHub: {
  label: "JupyterHub"
  hub: "Hub"
  nb1: "Notebook 1"
  nb2: "Notebook 2"
  nb3: "Notebook 3"
}

Dask: {
  label: "Dask Cluster"
  sched: "Scheduler"
  w1: "Worker 1"
  w2: "Worker 2"
  w3: "Worker 3"
  w4: "Worker 4"
}

S3: "S3 / Iceberg"

Users.u1 -> JupyterHub.nb1
Users.u2 -> JupyterHub.nb2
Users.u3 -> JupyterHub.nb3

JupyterHub.nb1 -> Dask.sched
JupyterHub.nb2 -> Dask.sched
JupyterHub.nb3 -> Dask.sched

Dask.sched -> Dask.w1
Dask.sched -> Dask.w2
Dask.sched -> Dask.w3
Dask.sched -> Dask.w4

Dask.w1 -> S3
Dask.w2 -> S3
```

## Deployment

### Dask Operator

```bash
# Deploy Dask operator
helm repo add dask https://helm.dask.org
helm install dask-operator dask/dask-kubernetes-operator \
  --namespace dask --create-namespace
```

### Dask Cluster

```yaml
# dask-cluster.yaml
apiVersion: kubernetes.dask.org/v1
kind: DaskCluster
metadata:
  name: analytics
  namespace: dask
spec:
  worker:
    replicas: 4
    spec:
      containers:
      - name: worker
        image: ghcr.io/dask/dask:latest
        resources:
          requests:
            memory: "8Gi"
            cpu: "2"
```

### JupyterHub

```bash
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
helm install jupyterhub jupyterhub/jupyterhub \
  --namespace jupyter --create-namespace \
  -f jupyterhub-values.yaml
```

## Connecting to Dask

### From Notebook

```python
from dask.distributed import Client

# Connect to cluster
client = Client("tcp://dask-scheduler.dask:8786")
print(client.dashboard_link)

# Or use Dask Gateway
from dask_gateway import Gateway
gateway = Gateway()
cluster = gateway.new_cluster()
client = cluster.get_client()
```

### Reading Iceberg Data

```python
import dask.dataframe as dd
from pyiceberg.catalog import load_catalog

# Load catalog
catalog = load_catalog("cybersec", **{
    "type": "rest",
    "uri": "http://polaris:8181/api/catalog",
    "warehouse": "cybersec"
})

# Get table as Dask DataFrame
table = catalog.load_table("security.cloudtrail")
scan = table.scan()
df = scan.to_dask()

# Process with Dask
result = df.groupby("eventSource").agg({
    "eventID": "count",
    "errorCode": lambda x: x.notna().sum()
}).compute()
```

## Multi-User Configuration

### Resource Quotas

```yaml
# user-quotas.yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: user-quota
  namespace: jupyter
spec:
  hard:
    requests.cpu: "8"
    requests.memory: "32Gi"
    limits.cpu: "16"
    limits.memory: "64Gi"
```

### Profile-Based Spawner

```yaml
# jupyterhub-values.yaml
singleuser:
  profileList:
    - display_name: "Small (2 CPU, 8GB)"
      kubespawner_override:
        cpu_limit: 2
        mem_limit: "8G"
    - display_name: "Large (8 CPU, 32GB)"
      kubespawner_override:
        cpu_limit: 8
        mem_limit: "32G"
    - display_name: "GPU (4 CPU, 16GB, 1 GPU)"
      kubespawner_override:
        cpu_limit: 4
        mem_limit: "16G"
        extra_resource_limits:
          nvidia.com/gpu: "1"
```

## Benchmarking Workflows

```python
import time
import dask.dataframe as dd

# Benchmark: Read 30GB dataset
start = time.time()
df = dd.read_parquet("s3://benchmark/events/")
count = df.shape[0].compute()
elapsed = time.time() - start

print(f"Read {count:,} rows in {elapsed:.2f}s")
print(f"Throughput: {count / elapsed:,.0f} rows/sec")
```

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
