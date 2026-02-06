Feature: Local Pipeline Development
  As a developer
  I want to run the data pipeline locally
  So I can develop and test changes quickly

  Background:
    Given the cybersec repository is cloned
    And git submodules are initialized

  @core @services
  Scenario: Start devenv and verify services
    When I run "devenv up"
    Then PostgreSQL should be running on port 5438
    And MinIO should be accessible on port 9010
    And Polaris REST API should respond on port 8181
    And Iceberg Browser should be accessible on port 5050

  @core @flink
  Scenario: Run CloudTrail datagen job
    Given devenv services are running
    And Flink is provisioned
    When I run "uv run python flink_jobs/cloudtrail_datagen.py"
    Then events should appear in the cloudtrail-raw Kafka topic
    And the Flink job should show as running in the Web UI

  @core @pipeline
  Scenario: Process and persist CloudTrail events
    Given cloudtrail-raw topic has events
    When I run the cloudtrail_processor job
    And I run the cloudtrail_writer job
    Then events should land in the Iceberg table
    And I can query them via Iceberg Browser

  @health
  Scenario: Health diagnostics pass
    Given devenv services are running
    When I run "cybersec health"
    Then all critical checks should pass
    And no failure modes should be detected

  @bootstrap
  Scenario: Bootstrap completes successfully
    Given devenv services are running
    When I run "cybersec bootstrap run"
    Then bootstrap should complete without errors
    And the cybersec catalog should exist in Polaris
