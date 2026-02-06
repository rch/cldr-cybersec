# Workstation Scenarios

BDD scenarios that define testable workflows for workstation deployment.

## Feature: RKE2 Deployment

### Scenario: Install and configure RKE2

```gherkin
Feature: RKE2 Deployment

  Scenario: Install and configure RKE2
    Given a workstation with 64GB RAM and NVIDIA GPU
    When I install RKE2 via the install script
    And I configure kubectl with the kubeconfig
    Then all RKE2 system pods should be running
    And I should be able to deploy workloads
```

### Scenario: Deploy Flink cluster on RKE2

```gherkin
  Scenario: Deploy Flink cluster on RKE2
    Given RKE2 is running and healthy
    When I apply the Flink manifests
    Then the Flink JobManager should be running
    And TaskManagers should register with the JobManager
    And the Flink Web UI should be accessible
```

## Feature: Cyber Toolkit Pipeline

### Scenario: Run parser chain with enrichment

```gherkin
Feature: Cyber Toolkit Pipeline

  Scenario: Run parser chain with enrichment
    Given Flink is deployed on RKE2
    And parser-chains-flink jar is built
    When I submit the parser chain job
    And I send CloudTrail events to the input topic
    Then events should be parsed according to the chain config
    And enrichments should be applied (CIDR, GeoIP)
    And enriched events should land in the output topic
```

### Scenario: End-to-end security pipeline

```gherkin
  Scenario: End-to-end security pipeline
    Given parser, enrichment, profiler, and indexer jobs are running
    When I send raw CloudTrail events
    Then events should flow through all pipeline stages
    And profiled events should be written to Iceberg
    And alert scores should be computed for suspicious events
```

## Feature: GPU Acceleration

### Scenario: Deploy GPU operator

```gherkin
Feature: GPU Acceleration

  Scenario: Deploy GPU operator
    Given RKE2 is running on a workstation with NVIDIA GPU
    When I install the NVIDIA GPU Operator via Helm
    Then GPU nodes should be labeled with nvidia.com/gpu.present
    And the device plugin should report available GPUs
```

### Scenario: Run ML inference with GPU

```gherkin
  Scenario: Run ML inference with GPU
    Given GPU operator is installed
    And an ONNX model is available
    When I deploy the anomaly detector with GPU limits
    Then the pod should be scheduled on a GPU node
    And inference should use GPU acceleration
    And latency should be under 10ms per event
```

## Feature File Location

These scenarios map to feature files in:

```
features/workstation/
├── rke2-deployment.feature
├── cyber-toolkit.feature
└── gpu-acceleration.feature
```

## Running Scenarios

```bash
# Run all workstation scenarios
uv run pytest features/workstation/ --bdd

# Run specific feature
uv run pytest features/workstation/cyber-toolkit.feature --bdd
```
