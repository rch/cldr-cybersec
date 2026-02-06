# Laptop Development Scenarios

BDD scenarios that define testable workflows for laptop development.

## Feature: Local Pipeline Development

### Scenario: Start devenv and verify services

```gherkin
Feature: Local Pipeline Development

  Scenario: Start devenv and verify services
    Given I have cloned the cybersec repository
    And git submodules are initialized
    When I run "devenv up"
    Then PostgreSQL should be running on port 5438
    And MinIO should be accessible on port 9010
    And Polaris REST API should respond on port 8181
    And Iceberg Browser should be accessible on port 5050
```

### Scenario: Run CloudTrail datagen job

```gherkin
  Scenario: Run CloudTrail datagen job
    Given devenv services are running
    And Flink is provisioned
    When I run "uv run python flink_jobs/cloudtrail_datagen.py"
    Then events should appear in the cloudtrail-raw Kafka topic
    And the Flink job should show as running in the Web UI
```

### Scenario: Process and persist CloudTrail events

```gherkin
  Scenario: Process and persist CloudTrail events
    Given cloudtrail-raw topic has events
    When I run the cloudtrail_processor job
    And I run the cloudtrail_writer job
    Then events should land in the Iceberg table
    And I can query them via Iceberg Browser
```

## Feature: K3d Development

### Scenario: Deploy Dask on K3d

```gherkin
Feature: K3d Development

  Scenario: Deploy Dask on K3d
    Given devenv services are running
    When I run "devenv tasks run k8s:provision"
    And I run "devenv tasks run k8s:deploy-dask"
    Then the Dask scheduler pod should be running
    And the Dask dashboard should be accessible on port 8787
```

### Scenario: Run Dask computation

```gherkin
  Scenario: Run Dask computation
    Given Dask is deployed on K3d
    And Iceberg tables contain data
    When I submit a Dask computation from a notebook
    Then the computation should distribute across workers
    And results should be written to Iceberg
```

## Feature File Location

These scenarios map to feature files in:

```
features/laptop-dev/
├── local-pipeline.feature
├── k3d-development.feature
└── health-diagnostics.feature
```

## Running Scenarios

```bash
# Run all laptop-dev scenarios
uv run pytest features/laptop-dev/ --bdd

# Run specific feature
uv run pytest features/laptop-dev/local-pipeline.feature --bdd
```
