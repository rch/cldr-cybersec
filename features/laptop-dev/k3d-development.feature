Feature: K3d Development
  As a developer
  I want to use K3d for Kubernetes testing
  So I can validate workloads before deploying to RKE2

  Background:
    Given devenv services are running

  @k3d @provision
  Scenario: Provision K3d cluster
    When I run "devenv tasks run k8s:provision"
    Then a k3d cluster should be created
    And kubectl should be configured for the cluster
    And the cluster should have 1 server node

  @k3d @dask
  Scenario: Deploy Dask on K3d
    Given K3d cluster is provisioned
    When I run "devenv tasks run k8s:deploy-dask"
    Then the Dask operator should be installed
    And a DaskCluster should be created
    And the Dask scheduler pod should be running
    And worker pods should be running

  @k3d @dask @compute
  Scenario: Run Dask computation
    Given Dask is deployed on K3d
    And Iceberg tables contain data
    When I submit a Dask computation from a notebook
    Then the computation should distribute across workers
    And results should be written to Iceberg

  @k3d @jupyter
  Scenario: Deploy JupyterHub on K3d
    Given K3d cluster is provisioned
    When I run "devenv tasks run k8s:deploy-jupyter"
    Then JupyterHub should be running
    And I can access the login page on port 8000

  @k3d @cleanup
  Scenario: Destroy K3d cluster
    Given K3d cluster is provisioned
    When I run "devenv tasks run k8s:destroy"
    Then the k3d cluster should be deleted
    And no k3d processes should remain
