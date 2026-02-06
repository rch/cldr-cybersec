# Workstation with GPUs

Run full security analysis workloads with GPU acceleration on a dedicated workstation.

## Overview

The workstation environment provides:

- Full Flink cluster with Cyber Toolkit enrichments
- RKE2 Kubernetes at the system level
- GPU-accelerated ML inference
- Local storage with Ozone or MinIO

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 8 cores | 16+ cores |
| RAM | 32GB | 64GB+ |
| GPU | 1x NVIDIA (8GB VRAM) | 2x NVIDIA (16GB+ VRAM) |
| Storage | 500GB SSD | 2TB NVMe |

## Environment Setup

### RKE2 Installation

```bash
# Install RKE2 (system-level)
curl -sfL https://get.rke2.io | sudo sh -

# Enable and start
sudo systemctl enable rke2-server
sudo systemctl start rke2-server

# Configure kubectl
mkdir -p ~/.kube
sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/rke2.yaml
sudo chown $USER ~/.kube/rke2.yaml
export KUBECONFIG=~/.kube/rke2.yaml
```

### Flink + Cyber Toolkit

```bash
# Build cyber toolkit (first time)
cd flink-cyber
mvn clean install -DskipTests

# Deploy Flink to RKE2
kubectl apply -f deploy/flink/
```

### GPU Operator

```bash
# Install NVIDIA GPU Operator
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator --create-namespace
```

## Workload Capabilities

| Capability | Technology | Use Case |
|------------|------------|----------|
| Stream Processing | Flink + Cyber Toolkit | Real-time enrichment |
| ML Inference | GPU + ONNX Runtime | Anomaly detection |
| Data Lake | Iceberg + Ozone | Long-term storage |
| Orchestration | RKE2 | Service deployment |

## Cyber Toolkit Components

The Java toolkit (`flink-cyber/`) provides:

- **Parser Chains**: Log parsing with flexible chain configuration
- **Enrichment**: CIDR lookup, geocoding, ThreatQ integration
- **Profiling**: Event profiling and aggregation
- **Scoring**: Alert scoring and prioritization

## Subchapters

- [Flink + Cyber Toolkit](./workstation/flink-cyber.md): Pipeline configuration
- [RKE2 System Deployment](./workstation/rke2.md): Kubernetes setup
- [GPU-Accelerated Workflows](./workstation/gpu.md): ML inference patterns
- [Scenarios](./workstation/scenarios.md): BDD scenarios for this workload
