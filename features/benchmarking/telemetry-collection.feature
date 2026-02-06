Feature: Telemetry Collection
  As a platform engineer
  I want to collect telemetry from local workloads
  So I can analyze performance across environments

  Background:
    Given devenv services are running locally
    And OTEL collector is configured

  @telemetry @metrics
  Scenario: Export Flink metrics to Prometheus
    Given Flink jobs are running
    When I query the local Prometheus
    Then Flink metrics should be available
    And job throughput metrics should be recorded
    And checkpoint metrics should be recorded

  @telemetry @remote
  Scenario: Export metrics to remote cluster
    Given remote RKE2 cluster is accessible
    And OTEL collector is configured for remote export
    When I run Flink jobs locally
    Then metrics should appear in local Prometheus
    And metrics should be exported to remote OTEL collector
    And remote Prometheus should show Flink metrics

  @telemetry @traces
  Scenario: Trace pipeline execution
    Given OTEL tracing is enabled for Python applications
    When I run the cloudtrail processing pipeline
    Then traces should be generated for each stage
    And traces should show parent-child relationships
    And span durations should be recorded accurately

  @telemetry @nifi
  Scenario: Visualize traces in NiFi
    Given NiFi is running and receiving OTEL traces
    When I send events through the pipeline
    Then NiFi should receive trace data
    And I can visualize the trace flow in NiFi UI
