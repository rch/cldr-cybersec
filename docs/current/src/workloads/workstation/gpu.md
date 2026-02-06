# GPU-Accelerated Workflows

Running ML inference and GPU-accelerated analytics on the workstation.

## GPU Support

### NVIDIA GPU Operator

The GPU Operator automates GPU driver and container toolkit setup:

```bash
# Add NVIDIA Helm repo
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

# Install GPU Operator
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator --create-namespace \
  --set driver.enabled=true \
  --set toolkit.enabled=true
```

### Verify GPU Access

```bash
# Check GPU nodes
kubectl get nodes -l nvidia.com/gpu.present=true

# Run GPU test pod
kubectl run gpu-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.0-base \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi
```

## ML Inference Patterns

### ONNX Runtime

For model serving with ONNX:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: anomaly-detector
spec:
  template:
    spec:
      containers:
      - name: onnx-server
        image: mcr.microsoft.com/onnxruntime/server:latest
        resources:
          limits:
            nvidia.com/gpu: 1
        volumeMounts:
        - name: models
          mountPath: /models
```

### Triton Inference Server

For multi-model serving:

```bash
helm install triton nvidia/triton-inference-server \
  --set image.modelRepositoryPath=s3://models/repository
```

## GPU Scheduling

### Time-Slicing (Multiple Workloads per GPU)

```yaml
# gpu-operator values
devicePlugin:
  config:
    sharing:
      timeSlicing:
        resources:
        - name: nvidia.com/gpu
          replicas: 4
```

### Multi-Instance GPU (MIG)

For A100/A30 GPUs:

```yaml
migManager:
  config:
    name: all-1g.5gb
```

## Dask with GPU

```python
from dask_cuda import LocalCUDACluster
from dask.distributed import Client

cluster = LocalCUDACluster()
client = Client(cluster)

# GPU DataFrame operations
import cudf
import dask_cudf

ddf = dask_cudf.read_parquet("s3://data/events/")
result = ddf.groupby("eventType").count().compute()
```

## Use Cases

| Use Case | Library | GPU Benefit |
|----------|---------|-------------|
| Anomaly Detection | PyTorch + ONNX | 10-50x inference speedup |
| Log Embedding | Sentence Transformers | Batch processing |
| Graph Analysis | cuGraph | Large-scale traversal |
| DataFrame Ops | cuDF + Dask | Memory bandwidth |

## Related Scenarios

See [Scenarios](./scenarios.md) for testable workflows.
