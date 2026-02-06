Feature: RKE2 Deployment
  As an operator
  I want to deploy workloads on RKE2
  So I can run production-grade security analytics

  Background:
    Given a workstation with 64GB RAM

  @rke2 @install
  Scenario: Install and configure RKE2
    When I install RKE2 via the install script
    And I start the rke2-server service
    And I configure kubectl with the kubeconfig
    Then all RKE2 system pods should be running
    And the node should be in Ready state

  @rke2 @flink
  Scenario: Deploy Flink cluster on RKE2
    Given RKE2 is running and healthy
    When I apply the Flink manifests
    Then the Flink JobManager should be running
    And TaskManagers should register with the JobManager
    And the Flink Web UI should be accessible

  @rke2 @storage
  Scenario: Configure Longhorn storage
    Given RKE2 is running and healthy
    When I install Longhorn via Helm
    Then the Longhorn storage class should be available
    And PVCs should be dynamically provisioned

  @rke2 @dask
  Scenario: Deploy Dask on RKE2
    Given RKE2 is running and healthy
    When I deploy the Dask operator
    And I create a DaskCluster resource
    Then the Dask scheduler should be running
    And worker pods should be scheduled on worker nodes
