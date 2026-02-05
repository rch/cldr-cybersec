# Comprehensive Environment Policy
#
# Validates ALL configuration required for:
# 1. Complete local Flink stack deployment
# 2. Remote AWS Dask stack deployment
#
# Run with: conftest test build/environment.json --policy policy/environment/
#
# This policy ensures that even with temporary library conflicts blocking
# certain features, all configuration is present for full functionality.

package environment.comprehensive

import rego.v1

# Use input directly (environment.json structure)
# Note: Some policies use input.effective for HOCON merged config,
# but environment.json places data at root level.
svc := input.services
tools := input.tools
packages := input.python.packages
aws := input.aws
flink := input.flink
ngrok := svc.ngrok
cloudflare := svc.cloudflare

# =============================================================================
# LOCAL FLINK STACK - Core Services
# =============================================================================

# PostgreSQL is required for Polaris catalog backend
deny contains msg if {
    not svc.postgres.healthy
    msg := "PostgreSQL not healthy. Required for Polaris catalog. Run: devenv up"
}

# MinIO is required for S3-compatible object storage
deny contains msg if {
    not svc.minio.healthy
    msg := "MinIO not healthy. Required for Iceberg storage. Run: devenv up"
}

# Polaris is required for Iceberg catalog management
deny contains msg if {
    not svc.polaris.healthy
    msg := "Polaris not healthy. Required for Iceberg catalog. Run: devenv up"
}

# Flink JobManager is required for job submission
deny contains msg if {
    flink.home_exists
    not svc.flink.jobmanager_healthy
    msg := "Flink JobManager not healthy. Run: devenv tasks run restart:clean"
}

# TaskManagers are required for job execution
deny contains msg if {
    svc.flink.jobmanager_healthy
    svc.flink.taskmanager_count == 0
    msg := "No Flink TaskManagers. Jobs cannot execute. Check TaskManager logs."
}

# =============================================================================
# LOCAL FLINK STACK - Observability Services
# =============================================================================

# NiFi is required for data flow visualization
warn contains msg if {
    not svc.nifi.healthy
    msg := "NiFi not healthy on port 8450. Data flow visualization unavailable."
}

# OTEL Collector is required for telemetry
warn contains msg if {
    not svc.otel_collector.healthy
    msg := "OpenTelemetry Collector not healthy. Metrics/traces unavailable."
}

# Prometheus is required for metrics storage
warn contains msg if {
    not svc.prometheus.healthy
    msg := "Prometheus not healthy on port 9090. Metrics storage unavailable."
}

# Iceberg Browser is required for data exploration
warn contains msg if {
    not svc.iceberg_browser.healthy
    msg := "Iceberg Browser not healthy on port 5050. Data exploration unavailable."
}

# =============================================================================
# LOCAL FLINK STACK - PyFlink Environment
# =============================================================================

# FLINK_HOME must exist
deny contains msg if {
    not flink.home_exists
    msg := "FLINK_HOME directory does not exist. Build Flink or run: devenv tasks run restart:clean"
}

# Flink binary must exist
deny contains msg if {
    flink.home_exists
    not flink.binary_exists
    msg := "Flink binary not found. Run: cd thirdparty/flink && mvn clean install -DskipTests -Dfast"
}

# PyFlink package must be installed
deny contains msg if {
    not packages.apache_flink
    msg := "PyFlink not installed. Run: uv pip install apache-flink"
}

# PyIceberg package must be installed
deny contains msg if {
    not packages.pyiceberg
    msg := "PyIceberg not installed. Run: uv pip install pyiceberg"
}

# httpx package must be installed
deny contains msg if {
    not packages.httpx
    msg := "httpx not installed. Run: uv pip install httpx"
}

# =============================================================================
# DEPLOYMENT TOOLS - Always Required
# =============================================================================

# conftest is required for policy validation
deny contains msg if {
    not tools.conftest
    msg := "conftest not installed. Required for policy validation. Run: nix-env -iA nixpkgs.conftest"
}

# =============================================================================
# AWS DASK STACK - Credentials
# =============================================================================

# ngrok auth token is always required
deny contains msg if {
    not ngrok.auth_token_set
    msg := "NGROK_AUTH_TOKEN not set. Required for all deployments. Get from: https://dashboard.ngrok.com/get-started/your-authtoken"
}

# ngrok API key is always required
deny contains msg if {
    not ngrok.api_key_set
    msg := "NGROK_API_KEY not set. Required for ngrok operator. Get from: https://dashboard.ngrok.com/api"
}

# Cloudflare API token is required for custom domains
deny contains msg if {
    ngrok.credentials_complete
    not cloudflare.api_token_set
    msg := "CLOUDFLARE_API_TOKEN not set. Required for custom domain DNS management."
}

# AWS credentials must be configured
deny contains msg if {
    not aws.credentials_configured
    aws.error != null
    aws.remediation == null
    msg := sprintf("AWS credentials not configured: %s. Required for AWS deployments.", [aws.error])
}

# AWS credentials with remediation guidance
deny contains msg if {
    not aws.credentials_configured
    aws.error != null
    aws.remediation != null
    msg := sprintf("AWS credentials not configured: %s\n\nRemediation:\n%s", [aws.error, aws.remediation])
}

# AWS S3 access is required
deny contains msg if {
    aws.credentials_configured
    not aws.permissions.s3_access
    msg := "AWS S3 access denied. Check IAM permissions for s3:ListBuckets, s3:PutObject, s3:GetObject."
}

# =============================================================================
# AWS DASK STACK - Deployment Tools
# =============================================================================

# Infrastructure-as-code tool required (tofu or terraform)
deny contains msg if {
    tools.iac_tool == null
    msg := "Neither tofu nor terraform installed. Required for AWS infrastructure. Install OpenTofu: https://opentofu.org/docs/intro/install/"
}

# Ansible is required for configuration management
deny contains msg if {
    not tools.ansible_playbook
    msg := "ansible-playbook not installed. Required for AWS deployment. Run: uv pip install ansible"
}

# AWS CLI is required for deployment operations
deny contains msg if {
    not tools.aws_cli
    msg := "AWS CLI not installed. Required for AWS deployments. Install from: https://aws.amazon.com/cli/"
}

# SSH key is required for AWS instance access
deny contains msg if {
    not tools.ssh_key_exists
    msg := "No SSH key found. Required for AWS instance access. Create: ssh-keygen -t ed25519 -f ~/.ssh/cybersec-dask.pem"
}

# =============================================================================
# AWS DASK STACK - Kubernetes Tools
# =============================================================================

# kubectl is required for K8s operations
deny contains msg if {
    not tools.kubectl
    msg := "kubectl not installed. Required for Kubernetes operations."
}

# Helm is required for chart deployments
deny contains msg if {
    not tools.helm
    msg := "Helm not installed. Required for deploying Dask, JupyterHub, ngrok. Install from: https://helm.sh/docs/intro/install/"
}

# =============================================================================
# AWS DASK STACK - AWS Permissions (Warnings)
# =============================================================================

# EC2 describe permission is needed for verification
warn contains msg if {
    aws.credentials_configured
    not aws.permissions.ec2_describe
    msg := "AWS EC2 describe permission unavailable. Deployment verification may fail."
}

# =============================================================================
# INFORMATIONAL - Status Messages
# =============================================================================

# Local Flink stack status
info contains msg if {
    svc.postgres.healthy
    svc.minio.healthy
    svc.polaris.healthy
    svc.flink.jobmanager_healthy
    svc.flink.taskmanager_count > 0
    msg := sprintf("Local Flink stack healthy: %d TaskManager slots available", [svc.flink.slots_available])
}

# Python packages status
info contains msg if {
    packages.flink_stack_complete
    msg := "Flink Python packages complete (pyflink, pyiceberg, httpx)"
}

info contains msg if {
    packages.dask_stack_complete
    msg := "Dask Python packages complete (dask, distributed)"
}

# AWS credentials status
info contains msg if {
    aws.credentials_configured
    msg := sprintf("AWS credentials configured (account: %s, identity: %s)", [aws.account_id, aws.arn])
}

# ngrok credentials status
info contains msg if {
    ngrok.credentials_complete
    cloudflare.api_token_set
    msg := sprintf("External access configured: ngrok + Cloudflare (domains: %s, %s)", [ngrok.domains.dask, ngrok.domains.jupyterhub])
}

# Deployment tools status
info contains msg if {
    tools.iac_tool != null
    tools.ansible_playbook
    tools.ssh_key_exists
    msg := sprintf("AWS deployment tools ready (IaC: %s, SSH key: %s)", [tools.iac_tool, tools.ssh_key_path])
}
