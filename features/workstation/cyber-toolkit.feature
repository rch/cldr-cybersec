Feature: Cyber Toolkit Pipeline
  As a security analyst
  I want to run the Cyber Toolkit enrichment pipeline
  So I can analyze security events with full context

  Background:
    Given Flink is deployed on RKE2
    And the flink-cyber jars are built

  @cyber @parser
  Scenario: Run parser chain with enrichment
    Given parser-chains-flink jar is available
    When I submit the parser chain job
    And I send CloudTrail events to the input topic
    Then events should be parsed according to the chain config
    And enrichments should be applied
    And enriched events should land in the output topic

  @cyber @enrichment
  Scenario: Apply CIDR and GeoIP enrichments
    Given raw events contain source IP addresses
    When I run the enrichment pipeline
    Then events should have CIDR range annotations
    And events should have geographic location data
    And events should have ASN organization info

  @cyber @pipeline @e2e
  Scenario: End-to-end security pipeline
    Given parser, enrichment, profiler, and indexer jobs are running
    When I send raw CloudTrail events
    Then events should flow through all pipeline stages
    And profiled events should be written to Iceberg
    And alert scores should be computed for suspicious events

  @cyber @profiler
  Scenario: Profile event patterns
    Given events are flowing through the pipeline
    When the profiler job processes events
    Then user activity profiles should be computed
    And anomalous patterns should be flagged
    And profile data should be queryable

  @cyber @scoring
  Scenario: Score alerts
    Given profiled events are available
    When the alert scoring job runs
    Then high-risk events should be identified
    And scores should be based on multiple factors
    And scored alerts should be queryable by severity
