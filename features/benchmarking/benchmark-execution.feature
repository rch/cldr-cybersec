Feature: Benchmark Execution
  As a performance engineer
  I want to run benchmarks across environments
  So I can validate throughput and latency characteristics

  Background:
    Given local and remote environments are healthy

  @benchmark @throughput
  Scenario: Stream processing throughput test
    Given local Flink cluster is running
    And datagen is configured for 10K events/second
    When I run the full pipeline for 5 minutes
    Then throughput should maintain 10K events/second
    And end-to-end latency should be under 5 seconds
    And no backpressure should be observed

  @benchmark @dask @large
  Scenario: Run 30GB dataset benchmark
    Given 30GB of CloudTrail events in S3
    And Dask cluster has 4 workers with 8GB each
    When I read the full dataset as a Dask DataFrame
    And I compute aggregations by eventSource
    Then the read should complete in under 60 seconds
    And the aggregation should complete in under 30 seconds
    And peak memory per worker should stay under 7GB

  @benchmark @comparison
  Scenario: Cross-environment comparison
    Given local Flink is processing events
    And remote Dask is analyzing historical data
    When I run the same query on both environments
    Then I can compare execution times
    And I can identify bottlenecks in each environment
    And results should be consistent between environments

  @benchmark @iceberg
  Scenario: Iceberg write performance
    Given empty Iceberg table exists
    When I write 1 million events in batches of 10K
    Then total write time should be under 2 minutes
    And each commit should complete in under 5 seconds
    And no snapshot conflicts should occur

  @benchmark @checkpoint
  Scenario: Flink checkpoint performance
    Given Flink job is running with checkpointing enabled
    When I trigger 10 checkpoints
    Then each checkpoint should complete in under 30 seconds
    And checkpoint size should be under 1GB
    And no checkpoint failures should occur
