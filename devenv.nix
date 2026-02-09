{ pkgs, lib, config, inputs, ... }:

{
  dotenv.enable = true;

  # MinIO data directory - uses DEVENV_STATE by default
  # Override in .cybersec/config.toml or set MINIO_DATA_DIR env var
  # env.MINIO_DATA_DIR = lib.mkForce "/opt/minio/cybersec";  # Example override

  # MinIO credentials - separate from AWS to avoid conflicts
  # Python code checks MINIO_* first when S3_ENDPOINT is set (local MinIO)
  # AWS CLI uses ~/.aws/credentials (default profile) for real AWS operations
  env.MINIO_ACCESS_KEY = "minioadmin";
  env.MINIO_SECRET_KEY = "minioadmin";
  env.S3_ENDPOINT = "http://localhost:9010";

  # Flink home - built from source in thirdparty/flink
  env.FLINK_HOME = "${config.devenv.root}/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1";
  env.KUBECONFIG = "${config.devenv.root}/.devenv/state/kubeconfig";


  # https://devenv.sh/packages/
  packages = with pkgs; [
    awscli2
    conftest
    d2
    dbmate
    flatbuffers
    flink
    git
    gh
    graphviz
    grpcurl
    imagemagick
    jq
    k3d
    kubectl
    kubernetes-helm
    mdbook
    mdbook-d2
    mdbook-katex
    mdbook-mermaid
    opentofu
    podman
    protobuf
    presenterm
    tilt
    zlib  # Required for numpy C extensions   
  ];

  services.minio = {
    enable = true;
    buckets = ["cybersec" "cybersec-hx"];
    listenAddress = "0.0.0.0:9010";
    consoleAddress = "0.0.0.0:9011";
  };

  services.postgres = {
    enable = true;
    package = pkgs.postgresql_16;
    extensions = ext: [
      ext.pg_cron  # Scheduled tasks
      ext.age      # Apache AGE - Graph database extension for lineage
    ];
    initialDatabases = [
      { name = "cybersec"; }
      { name = "metaflow"; }
      { name = "iceberg"; }
    ];
    port = 5438;
    listen_addresses = "*";  # Enable TCP from K8s pods and local clients
    settings = {
      shared_preload_libraries = "pg_cron,age";
      "cron.database_name" = "cybersec";
    };
    initialScript = ''
      -- Create cybersec user with login privileges
      CREATE USER cybersec WITH PASSWORD 'cybersec' LOGIN;
      
      -- Grant privileges on cybersec database
      GRANT ALL PRIVILEGES ON DATABASE cybersec TO cybersec;
      GRANT ALL PRIVILEGES ON DATABASE iceberg TO cybersec;
      
      -- Connect to iceberg database to create Polaris schema
      \c iceberg
      CREATE SCHEMA IF NOT EXISTS polaris_schema;
      GRANT ALL PRIVILEGES ON SCHEMA polaris_schema TO cybersec;
      ALTER SCHEMA polaris_schema OWNER TO cybersec;
      
      -- Create extensions
      \c cybersec
      CREATE EXTENSION IF NOT EXISTS pg_cron;
      -- AGE extension is created per-database in migrations
    '';
  };
  
  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv.enable = true;
    uv.sync.enable = true;
    # Install flink extra by default (for local PyFlink development).
    # The k8s extra (dask) conflicts with flink and must be installed separately.
    uv.sync.allExtras = false;
    uv.sync.extras = ["flink" "dev"];
    venv.enable = true;
  };

  languages.java = {
    enable = true;
    jdk.package = pkgs.jdk21;  # NiFi 2.0 requires Java 21+
    maven.enable = true;
  };

  languages.javascript = {
    enable = true;
    directory = "local-ui";
    npm = {
      enable = true;
      install.enable = true;
    };
  };

  # Shell initialization - runs on 'direnv allow' / entering the devenv shell
  # NOTE: Heavy operations (git submodule, Flink build) are handled by flink-bootstrap process
  # to avoid blocking shell startup. For first-time setup, run: devenv tasks run restart:clean
  enterShell = ''
    # Short alias for OpenTofu CLI
    alias tf="tofu"

    # macOS: prefer Podman machine connection for k3d/docker clients
    if [ "$(uname -s)" = "Darwin" ] && command -v podman >/dev/null 2>&1; then
      if [ -z "''${DOCKER_HOST:-}" ]; then
        if PODMAN_CONNS=$(podman system connection list --format json 2>/dev/null); then
          DEFAULT_URI=$(echo "$PODMAN_CONNS" | jq -r 'map(select(.Default==true)) | .[0].URI // empty')
          if [ -z "$DEFAULT_URI" ]; then
            DEFAULT_URI=$(echo "$PODMAN_CONNS" | jq -r 'map(select(.ReadWrite==true)) | .[0].URI // empty')
          fi
          if [ -n "$DEFAULT_URI" ]; then
            export DOCKER_HOST="$DEFAULT_URI"
            export K3D_HIDE_WARNING_ROOTLESS=1
            echo "Using Podman connection $DOCKER_HOST"
          fi
        fi
      fi
    fi

    # Auto-prepare submodules (init only, no branch checkout)
    # This runs quickly and ensures submodules are initialized on direnv allow
    if [ -f "cybersec/bootstrap/submodules.py" ]; then
      uv run python -c "
from cybersec.bootstrap.submodules import prepare_all_submodules, is_submodule_initialized
import sys

# Check if any submodule needs initialization
needs_init = []
for name in ['flink', 'polaris', 'iceberg']:
    if not is_submodule_initialized(name):
        needs_init.append(name)

if needs_init:
    print(f'Initializing submodules: {needs_init}')
    results = prepare_all_submodules()
    for name, (ok, msg) in results.items():
        if not ok:
            print(f'  Warning: {msg}', file=sys.stderr)
" 2>/dev/null || true
    fi

    # Create Polaris bin wrapper scripts if needed
    POLARIS_HOME="$PWD/thirdparty/polaris/polaris-bin-1.3.0-incubating"
    if [ -d "$POLARIS_HOME" ] && [ ! -x "$POLARIS_HOME/bin/admin" ]; then
      echo "Creating Polaris bin wrapper scripts..."
      "$PWD/scripts/setup_polaris_bin.sh" "$POLARIS_HOME" 2>/dev/null || true
    fi

    # First-time setup hint - check for missing builds, not just submodules
    NEEDS_BOOTSTRAP=false
    if [ ! -f "thirdparty/flink/pom.xml" ]; then
      NEEDS_BOOTSTRAP=true
    elif [ ! -d "thirdparty/flink/flink-dist/target/flink-1.20.1-bin" ]; then
      NEEDS_BOOTSTRAP=true
    elif [ ! -d "$POLARIS_HOME/server" ]; then
      NEEDS_BOOTSTRAP=true
    fi

    if [ "$NEEDS_BOOTSTRAP" = "true" ]; then
      echo ""
      echo "First-time setup or missing builds detected."
      echo "Run: devenv tasks run restart:clean"
      echo "This initializes submodules and builds Flink + Polaris (~15 min on first run)"
      echo ""
    fi

    # Kubernetes target detection (from KUBECONFIG only, no ENABLE_K8S fallback)
    # Priority: 1) Explicit CYBERSEC_K8S_TARGET, 2) KUBECONFIG contents
    if [ -z "''${CYBERSEC_K8S_TARGET:-}" ]; then
      DETECTED_K8S_TARGET="none"
      if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
        if grep -qE "rancher|rke2" "$KUBECONFIG" 2>/dev/null; then
          DETECTED_K8S_TARGET="rke2"
        elif grep -qE "k3d|k3s" "$KUBECONFIG" 2>/dev/null; then
          DETECTED_K8S_TARGET="k3d"
        fi
      fi
      export CYBERSEC_K8S_TARGET="$DETECTED_K8S_TARGET"
    fi
  '';
  
  languages.typescript = {
    enable=true;
  }; 

  tasks = {
    "docs:build".exec = "mdbook build docs/current";
    "docs:open".exec = "mdbook build docs/current --open";

    # Policy validation using conftest
    "policy:check".exec = ''
      echo "Running policy validation..."

      # Generate environment config
      uv run python -c "
import asyncio
from cybersec.health.environment import write_environment_config
asyncio.run(write_environment_config())
print('Environment config written to build/environment.json')
"

      echo ""
      echo "Running conftest policies..."
      conftest test build/environment.json --policy policy/environment/ --all-namespaces || {
        echo ""
        echo "Policy violations detected. Fix issues above and re-run."
        exit 1
      }
      echo ""
      echo "All policy checks passed"
    '';

    "policy:generate".exec = ''
      echo "Generating environment config..."
      uv run python -c "
import asyncio
from cybersec.health.environment import write_environment_config
asyncio.run(write_environment_config())
print('Environment config written to build/environment.json')
"
      echo ""
      echo "Config written. Run validation with:"
      echo "  conftest test build/environment.json --policy policy/environment/"
    '';

    # Manual Polaris catalog initialization (normally runs automatically via polaris-init process)
    # Use this if automatic initialization failed or you need to re-initialize
    "polaris:init".exec = ''
      source scripts/polaris_bootstrap_helper.sh
      
      log_info "Manually initializing Polaris catalog..."
      
      # Check if Polaris is running
      if ! wait_for_polaris 1 0; then
        log_error "Polaris is not running. Start it with: devenv up"
        exit 1
      fi
      
      # Trigger catalog initialization with retry
      if trigger_catalog_init 3 ./setup_polaris_catalog.sh; then
        log_success "Catalog initialized and verified successfully"
      else
        log_error "Catalog initialization failed after retries"
        exit 1
      fi
    '';
    
    # Verify complete Polaris bootstrap status
    "polaris:bootstrap-verify".exec = ''
      source scripts/polaris_bootstrap_helper.sh
      verify_all
    '';
    
    "polaris:check".exec = ''
      source scripts/polaris_bootstrap_helper.sh
      
      echo "Checking Polaris catalog status..."
      
      # Check if Polaris is running
      if ! wait_for_polaris 1 0; then
        echo "Polaris is not running. Start it with: devenv up"
        exit 1
      fi
      
      # Check if catalog exists
      if verify_catalog "cybersec"; then
        echo "Polaris is properly configured"
      else
        echo "Catalog 'cybersec' not found"
        echo "   This should have been created automatically by the polaris-init process"
        echo "   To initialize manually, run: devenv tasks run polaris:init"
        exit 1
      fi
    '';
    
    # ============================================================================
    # AWS Deployment Tasks
    # ============================================================================
    # These tasks manage the AWS infrastructure and Kubernetes deployments.
    # Prerequisites: AWS credentials configured, SSH key at ~/.ssh/cybersec-dask.pem

    "aws:provision".exec = ''
      echo "🚀 Provisioning AWS infrastructure with OpenTofu..."
      cd infra/aws/tofu

      if [ ! -f .terraform.lock.hcl ]; then
        echo "Initializing Tofu..."
        tofu init
      fi

      echo ""
      echo "Running tofu plan..."
      tofu plan -out=tfplan

      echo ""
      read -p "Apply this plan? [y/N] " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        tofu apply tfplan
        echo ""
        echo "✅ Infrastructure provisioned"
        echo ""
        echo "Next steps:"
        echo "  1. Update inventory: devenv tasks run aws:inventory"
        echo "  2. Deploy cluster: devenv tasks run aws:deploy"
      else
        echo "Aborted."
      fi
    '';

    "aws:destroy".exec = ''
      echo "⚠️  Destroying AWS infrastructure..."
      cd infra/aws/tofu

      echo ""
      tofu plan -destroy

      echo ""
      read -p "Destroy all resources? This cannot be undone! [y/N] " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        tofu destroy -auto-approve
        echo "✅ Infrastructure destroyed"
      else
        echo "Aborted."
      fi
    '';

    # Complete teardown - empties S3 bucket and destroys all infrastructure
    # Use this to avoid overnight AWS costs
    "aws:teardown".exec = ''
      echo "🗑️  COMPLETE AWS TEARDOWN"
      echo "========================="
      echo ""
      echo "This will:"
      echo "  1. Delete ALL data from the S3 bucket (including all versions)"
      echo "  2. Destroy ALL AWS infrastructure (EC2, VPC, IAM, etc.)"
      echo ""
      echo "⚠️  THIS CANNOT BE UNDONE!"
      echo ""

      cd infra/aws/tofu

      # Get bucket name from Tofu state
      BUCKET_NAME=$(tofu output -raw s3_bucket_name 2>/dev/null || echo "")

      if [ -z "$BUCKET_NAME" ]; then
        echo "No S3 bucket found in Tofu state."
        echo "Proceeding with infrastructure destroy only..."
      else
        echo "S3 Bucket: $BUCKET_NAME"

        # Check bucket contents
        OBJECT_COUNT=$(aws s3 ls "s3://$BUCKET_NAME" --recursive 2>/dev/null | wc -l || echo "0")
        echo "Objects in bucket: ~$OBJECT_COUNT"
        echo ""
      fi

      read -p "Proceed with complete teardown? [y/N] " -n 1 -r
      echo
      if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
      fi

      # Step 1: Empty S3 bucket (required before Tofu can delete it)
      if [ -n "$BUCKET_NAME" ]; then
        echo ""
        echo "Step 1/2: Emptying S3 bucket..."

        # Delete all object versions (required for versioned buckets)
        echo "  Deleting all object versions..."
        aws s3api list-object-versions --bucket "$BUCKET_NAME" --output json 2>/dev/null | \
          jq -r '.Versions[]? | "\(.Key) \(.VersionId)"' | \
          while read key version; do
            [ -n "$key" ] && aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" 2>/dev/null
          done

        # Delete all delete markers
        echo "  Deleting delete markers..."
        aws s3api list-object-versions --bucket "$BUCKET_NAME" --output json 2>/dev/null | \
          jq -r '.DeleteMarkers[]? | "\(.Key) \(.VersionId)"' | \
          while read key version; do
            [ -n "$key" ] && aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" 2>/dev/null
          done

        # Final cleanup with aws s3 rm (catches anything missed)
        aws s3 rm "s3://$BUCKET_NAME" --recursive 2>/dev/null || true

        echo "  ✅ S3 bucket emptied"
      fi

      # Step 2: Destroy infrastructure
      echo ""
      echo "Step 2/2: Destroying infrastructure with Tofu..."
      tofu destroy -auto-approve

      echo ""
      echo "✅ TEARDOWN COMPLETE"
      echo ""
      echo "All AWS resources have been destroyed."
      echo "No further charges will be incurred for this infrastructure."
    '';

    # Just clean S3 data without destroying infrastructure
    "aws:s3:clean".exec = ''
      echo "🧹 Cleaning S3 validation data..."
      cd infra/aws/tofu

      BUCKET_NAME=$(tofu output -raw s3_bucket_name 2>/dev/null || echo "")

      if [ -z "$BUCKET_NAME" ]; then
        echo "❌ No S3 bucket found in Tofu state."
        exit 1
      fi

      echo "Bucket: $BUCKET_NAME"
      echo ""
      echo "Listing data directories..."
      aws s3 ls "s3://$BUCKET_NAME/" 2>/dev/null || true
      echo ""

      read -p "Delete all data in s3://$BUCKET_NAME? [y/N] " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting objects (this may take a while for large datasets)..."

        # Delete all object versions
        aws s3api list-object-versions --bucket "$BUCKET_NAME" --output json 2>/dev/null | \
          jq -r '.Versions[]? | "\(.Key)\t\(.VersionId)"' | \
          while IFS=$'\t' read key version; do
            [ -n "$key" ] && aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" 2>/dev/null
          done

        # Delete delete markers
        aws s3api list-object-versions --bucket "$BUCKET_NAME" --output json 2>/dev/null | \
          jq -r '.DeleteMarkers[]? | "\(.Key)\t\(.VersionId)"' | \
          while IFS=$'\t' read key version; do
            [ -n "$key" ] && aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" 2>/dev/null
          done

        echo "✅ S3 bucket cleaned"
      else
        echo "Aborted."
      fi
    '';

    # Empty a specific S3 bucket by name - useful when tofu state is destroyed
    # Usage: S3_BUCKET=bucket-name devenv tasks run aws:s3:empty
    "aws:s3:empty".exec = ''
      BUCKET_NAME="''${S3_BUCKET:-cybersec-dask-data}"
      AWS_REGION="''${AWS_REGION:-us-east-1}"

      echo "🗑️  Emptying S3 bucket: $BUCKET_NAME (region: $AWS_REGION)"
      echo ""

      # Check if bucket exists
      if ! aws s3api head-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
        echo "❌ Bucket '$BUCKET_NAME' does not exist or is not accessible."
        exit 1
      fi

      # Count objects
      OBJECT_COUNT=$(aws s3 ls "s3://$BUCKET_NAME" --recursive --region "$AWS_REGION" 2>/dev/null | wc -l || echo "0")
      echo "Objects in bucket: ~$OBJECT_COUNT"
      echo ""

      read -p "Delete ALL objects from s3://$BUCKET_NAME? [y/N] " -n 1 -r
      echo
      if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
      fi

      echo ""
      echo "Step 1: Deleting all object versions..."
      aws s3api list-object-versions --bucket "$BUCKET_NAME" --region "$AWS_REGION" --output json 2>/dev/null | \
        jq -r '.Versions[]? | "\(.Key)\t\(.VersionId)"' | \
        while IFS=$'\t' read -r key version; do
          [ -n "$key" ] && [ -n "$version" ] && \
            aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" --region "$AWS_REGION" 2>/dev/null
        done

      echo "Step 2: Deleting delete markers..."
      aws s3api list-object-versions --bucket "$BUCKET_NAME" --region "$AWS_REGION" --output json 2>/dev/null | \
        jq -r '.DeleteMarkers[]? | "\(.Key)\t\(.VersionId)"' | \
        while IFS=$'\t' read -r key version; do
          [ -n "$key" ] && [ -n "$version" ] && \
            aws s3api delete-object --bucket "$BUCKET_NAME" --key "$key" --version-id "$version" --region "$AWS_REGION" 2>/dev/null
        done

      echo "Step 3: Final cleanup with aws s3 rm..."
      aws s3 rm "s3://$BUCKET_NAME" --recursive --region "$AWS_REGION" 2>/dev/null || true

      # Verify bucket is empty
      REMAINING=$(aws s3 ls "s3://$BUCKET_NAME" --recursive --region "$AWS_REGION" 2>/dev/null | wc -l || echo "0")
      if [ "$REMAINING" -eq 0 ]; then
        echo ""
        echo "✅ S3 bucket emptied successfully"
      else
        echo ""
        echo "⚠️  Warning: $REMAINING objects may remain. Run again if needed."
      fi
    '';

    "aws:inventory".exec = ''
      echo "📋 Generating Ansible inventory from Tofu outputs..."
      cd infra/aws/tofu

      # Get outputs
      BASTION_IP=$(tofu output -raw bastion_public_ip 2>/dev/null || echo "")
      CONTROL_PLANE_IPS=$(tofu output -json control_plane_private_ips 2>/dev/null || echo "[]")
      WORKER_IPS=$(tofu output -json worker_private_ips 2>/dev/null || echo "[]")
      K8S_API=$(tofu output -raw k8s_api_endpoint 2>/dev/null || echo "")

      if [ -z "$BASTION_IP" ]; then
        echo "❌ No bastion IP found. Run 'devenv tasks run aws:provision' first."
        exit 1
      fi

      INVENTORY_FILE="../ansible/inventory/hosts"
      cat > "$INVENTORY_FILE" << EOF
# Ansible inventory generated by OpenTofu
# SSH through bastion: ssh -J ec2-user@$BASTION_IP ec2-user@<private_ip>

[bastion]
$BASTION_IP ansible_user=ec2-user ansible_ssh_common_args='-o StrictHostKeyChecking=no'

[control_plane]
EOF

      # Add control plane nodes
      echo "$CONTROL_PLANE_IPS" | jq -r '.[] // empty' | nl -v 1 | while read num ip; do
        echo "$ip ansible_user=ec2-user rke2_type=server node_name=control-plane-$num" >> "$INVENTORY_FILE"
      done

      cat >> "$INVENTORY_FILE" << EOF

[workers]
EOF

      # Add worker nodes
      echo "$WORKER_IPS" | jq -r '.[] // empty' | nl -v 1 | while read num ip; do
        echo "$ip ansible_user=ec2-user rke2_type=agent node_name=worker-$num" >> "$INVENTORY_FILE"
      done

      cat >> "$INVENTORY_FILE" << EOF

[rke2:children]
control_plane
workers

[all:vars]
ansible_ssh_private_key_file=~/.ssh/cybersec-dask.pem
ansible_ssh_common_args='-o ProxyCommand="ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@$BASTION_IP" -o StrictHostKeyChecking=no'
k8s_api_endpoint=$K8S_API
EOF

      echo "✅ Inventory written to $INVENTORY_FILE"
      echo ""
      cat "$INVENTORY_FILE"
    '';

    "aws:status".exec = ''
      echo "📊 AWS Cluster Status"
      echo "====================="
      echo ""

      # Check tofu state
      if [ -f infra/aws/tofu/terraform.tfstate ]; then
        cd infra/aws/tofu
        echo "Infrastructure:"
        echo "  Bastion:       $(tofu output -raw bastion_public_ip 2>/dev/null || echo 'N/A')"
        echo "  K8s API:       $(tofu output -raw k8s_api_endpoint 2>/dev/null || echo 'N/A')"
        echo "  Control Planes: $(tofu output -json control_plane_private_ips 2>/dev/null | jq -r 'length' || echo '0')"
        echo "  Workers:       $(tofu output -json worker_private_ips 2>/dev/null | jq -r 'length' || echo '0')"
        cd - > /dev/null
      else
        echo "Infrastructure: Not provisioned"
      fi

      echo ""

      # Check connectivity
      BASTION_IP=$(cd infra/aws/tofu && tofu output -raw bastion_public_ip 2>/dev/null || echo "")
      if [ -n "$BASTION_IP" ]; then
        echo "Connectivity:"
        if timeout 5 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 -i ~/.ssh/cybersec-dask.pem ec2-user@$BASTION_IP "echo OK" 2>/dev/null; then
          echo "  Bastion SSH:   ✅ OK"
        else
          echo "  Bastion SSH:   ❌ Failed"
        fi
      fi
    '';

    "aws:deploy".exec = ''
      echo "🚀 Deploying full RKE2 cluster with Dask..."
      export PROJECT_ROOT="$PWD"

      # Override MinIO credentials with real AWS credentials from profile
      export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile ''${AWS_PROFILE:-default})
      export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile ''${AWS_PROFILE:-default})
      export AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile ''${AWS_PROFILE:-default} 2>/dev/null || echo "")

      cd infra/aws/ansible
      ansible-playbook playbooks/site.yml
      echo ""
      echo "✅ Cluster deployment complete"
      echo ""
      echo "Next steps:"
      echo "  - Deploy ngrok:      devenv tasks run aws:deploy:ngrok"
      echo "  - Deploy JupyterHub: devenv tasks run aws:deploy:jupyterhub"
    '';

    "aws:deploy:dask".exec = ''
      echo "🚀 Deploying Dask operator..."
      cd infra/aws/ansible
      ansible-playbook playbooks/dask-only.yml
      echo "✅ Dask operator deployed"
    '';

    "aws:deploy:ngrok".exec = ''
      echo "🚀 Deploying ngrok operator with OAuth..."

      # Check required environment variables
      if [ -z "$NGROK_AUTH_TOKEN" ] && [ -z "$NGROK_AUTHTOKEN" ]; then
        echo "❌ NGROK_AUTH_TOKEN or NGROK_AUTHTOKEN environment variable required"
        exit 1
      fi
      if [ -z "$NGROK_API_KEY" ]; then
        echo "❌ NGROK_API_KEY environment variable required"
        exit 1
      fi
      if [ -z "$CLOUDFLARE_API_TOKEN" ]; then
        echo "❌ CLOUDFLARE_API_TOKEN environment variable required"
        exit 1
      fi

      cd infra/aws/ansible
      ansible-playbook playbooks/ngrok.yml
      echo ""
      echo "✅ ngrok operator deployed"
      echo ""
      echo "Access URLs (after DNS propagation):"
      echo "  - Dask Dashboard: https://dask.zndx.org"
      echo "  - K8s Dashboard:  https://k8s.zndx.org"
    '';

    "aws:deploy:jupyterhub".exec = ''
      echo "🚀 Deploying JupyterHub..."
      export PROJECT_ROOT="$PWD"

      # Override MinIO credentials with real AWS credentials from profile
      export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile ''${AWS_PROFILE:-default})
      export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile ''${AWS_PROFILE:-default})
      export AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile ''${AWS_PROFILE:-default} 2>/dev/null || echo "")

      cd infra/aws/ansible
      ansible-playbook playbooks/jupyterhub.yml
      echo "✅ JupyterHub deployed"
    '';

    "aws:deploy:panel-viz".exec = ''
      echo "🚀 Deploying Panel visualization service..."
      export PROJECT_ROOT="$PWD"

      # Override MinIO credentials with real AWS credentials from profile
      export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile ''${AWS_PROFILE:-default})
      export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile ''${AWS_PROFILE:-default})
      export AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile ''${AWS_PROFILE:-default} 2>/dev/null || echo "")

      cd infra/aws/ansible
      ansible-playbook panel-viz.yml
      echo ""
      echo "✅ Panel visualization deployed"
      echo ""
      echo "Access URL: https://viz.zndx.org"
    '';

    "aws:apply".exec = ''
      echo "🔄 Applying configuration changes..."
      echo ""
      echo "This runs all deployment playbooks to apply any changes."
      echo ""

      cd infra/aws/ansible

      # Run playbooks that are idempotent
      echo "Applying Dask configuration..."
      ansible-playbook playbooks/dask-only.yml

      # Check if ngrok credentials are available
      if [ -n "$NGROK_AUTH_TOKEN" ] || [ -n "$NGROK_AUTHTOKEN" ]; then
        echo ""
        echo "Applying ngrok configuration..."
        ansible-playbook playbooks/ngrok.yml
      else
        echo ""
        echo "Skipping ngrok (no credentials in environment)"
      fi

      echo ""
      echo "✅ Configuration applied"
    '';

    "aws:verify".exec = ''
      echo "🔍 Verifying AWS cluster services..."
      echo ""

      BASTION_IP=$(cd infra/aws/tofu && tofu output -raw bastion_public_ip 2>/dev/null || echo "")
      CONTROL_IP=$(cd infra/aws/tofu && tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty' || echo "")

      if [ -z "$BASTION_IP" ] || [ -z "$CONTROL_IP" ]; then
        echo "❌ Cluster not provisioned. Run 'devenv tasks run aws:provision' first."
        exit 1
      fi

      SSH_CMD="ssh -i ~/.ssh/cybersec-dask.pem -o ProxyCommand=\"ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@$BASTION_IP\" -o StrictHostKeyChecking=no ec2-user@$CONTROL_IP"
      KUBECTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

      echo "Kubernetes Nodes:"
      eval $SSH_CMD "$KUBECTL get nodes -o wide" 2>/dev/null || echo "  Failed to get nodes"

      echo ""
      echo "Dask Cluster:"
      eval $SSH_CMD "$KUBECTL get daskclusters -A" 2>/dev/null || echo "  No Dask clusters found"

      echo ""
      echo "ngrok Traffic Policies:"
      eval $SSH_CMD "$KUBECTL get ngroktrafficpolicy -A" 2>/dev/null || echo "  No ngrok policies found"

      echo ""
      echo "ngrok Ingresses:"
      eval $SSH_CMD "$KUBECTL get ingress -A -l app.kubernetes.io/managed-by=ngrok-operator" 2>/dev/null || echo "  No ngrok ingresses found"

      echo ""
      echo "ngrok Operator Logs (last 10 lines):"
      eval $SSH_CMD "$KUBECTL logs -n ngrok-system deployment/ngrok-operator-manager --tail=10" 2>/dev/null || echo "  Failed to get logs"

      echo ""
      echo "External Access Test:"
      for domain in dask.zndx.org k8s.zndx.org; do
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://$domain" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "200" ]; then
          echo "  $domain: ✅ $HTTP_CODE (OAuth redirect or OK)"
        elif [ "$HTTP_CODE" = "000" ]; then
          echo "  $domain: ❌ Connection failed"
        else
          echo "  $domain: ⚠️  $HTTP_CODE"
        fi
      done
    '';

    "aws:ssh".exec = ''
      BASTION_IP=$(cd infra/aws/tofu && tofu output -raw bastion_public_ip 2>/dev/null || echo "")

      if [ -z "$BASTION_IP" ]; then
        echo "❌ Cluster not provisioned. Run 'devenv tasks run aws:provision' first."
        exit 1
      fi

      TARGET="''${1:-bastion}"

      case "$TARGET" in
        bastion)
          echo "Connecting to bastion..."
          exec ssh -i ~/.ssh/cybersec-dask.pem -o StrictHostKeyChecking=no ec2-user@$BASTION_IP
          ;;
        control|cp)
          CONTROL_IP=$(cd infra/aws/tofu && tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty')
          echo "Connecting to control plane ($CONTROL_IP) via bastion..."
          exec ssh -i ~/.ssh/cybersec-dask.pem \
            -o ProxyCommand="ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@$BASTION_IP" \
            -o StrictHostKeyChecking=no ec2-user@$CONTROL_IP
          ;;
        *)
          echo "Usage: devenv tasks run aws:ssh [bastion|control|cp]"
          echo ""
          echo "  bastion  - Connect to bastion host (default)"
          echo "  control  - Connect to first control plane node"
          echo "  cp       - Alias for control"
          ;;
      esac
    '';

    "aws:logs:ngrok".exec = ''
      echo "📜 Fetching ngrok operator logs..."

      BASTION_IP=$(cd infra/aws/tofu && tofu output -raw bastion_public_ip 2>/dev/null || echo "")
      CONTROL_IP=$(cd infra/aws/tofu && tofu output -json control_plane_private_ips 2>/dev/null | jq -r '.[0] // empty' || echo "")

      if [ -z "$BASTION_IP" ] || [ -z "$CONTROL_IP" ]; then
        echo "❌ Cluster not provisioned."
        exit 1
      fi

      SSH_CMD="ssh -i ~/.ssh/cybersec-dask.pem -o ProxyCommand=\"ssh -i ~/.ssh/cybersec-dask.pem -W %h:%p -o StrictHostKeyChecking=no ec2-user@$BASTION_IP\" -o StrictHostKeyChecking=no ec2-user@$CONTROL_IP"
      KUBECTL="sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml"

      LINES="''${1:-50}"
      eval $SSH_CMD "$KUBECTL logs -n ngrok-system deployment/ngrok-operator-manager --tail=$LINES"
    '';

    # ============================================================================
    # Kubernetes Stack Tasks (on-demand provisioning)
    # ============================================================================

    "k8s:status".exec = ''
      echo "Kubernetes Stack Status"
      echo "========================"

      # Check for kubeconfig
      KCONFIG="''${KUBECONFIG:-$PWD/.devenv/state/kubeconfig}"
      if [ -f "$KCONFIG" ]; then
        echo "Kubeconfig: $KCONFIG"
        if kubectl --kubeconfig="$KCONFIG" cluster-info >/dev/null 2>&1; then
          echo "Cluster: Connected"
          kubectl --kubeconfig="$KCONFIG" get nodes
          echo ""
          kubectl --kubeconfig="$KCONFIG" get pods -A | grep -E "dask|jupyter" || echo "No Dask/JupyterHub pods"
        else
          echo "Cluster: Not reachable"
        fi
      else
        echo "No kubeconfig found"
        echo ""
        echo "To provision k3d:  devenv tasks run k8s:provision"
        echo "To use RKE2:       export KUBECONFIG=~/.kube/rke2.yaml"
      fi
    '';

    "k8s:provision".exec = ''
      # Provisions local k3d cluster
      source scripts/polaris_bootstrap_helper.sh

      log_info "Provisioning k3d Kubernetes cluster..."

      # Check podman (macOS)
      if [ "$(uname -s)" = "Darwin" ]; then
        if command -v podman >/dev/null 2>&1; then
          if ! podman machine inspect podman-machine-default >/dev/null 2>&1; then
            log_info "Creating Podman machine..."
            podman machine init --cpus 4 --memory 8192
          fi
          if ! podman machine inspect podman-machine-default 2>/dev/null | grep -q '"Running": true'; then
            log_info "Starting Podman machine..."
            podman machine start podman-machine-default
          fi
        fi
      fi

      # Create k3d cluster
      CLUSTER_NAME="''${K3D_CLUSTER_NAME:-cybersec}"
      if k3d cluster list 2>/dev/null | grep -q "$CLUSTER_NAME"; then
        log_info "Cluster '$CLUSTER_NAME' already exists"
      else
        log_info "Creating k3d cluster '$CLUSTER_NAME'..."
        k3d cluster create "$CLUSTER_NAME" \
          --api-port 6550 \
          --servers 1 \
          --agents 0 \
          --k3s-arg "--disable=traefik@server:0" \
          --k3s-arg "--disable=servicelb@server:0"
      fi

      # Generate kubeconfig
      KCONFIG="$PWD/.devenv/state/kubeconfig"
      mkdir -p "$(dirname "$KCONFIG")"
      k3d kubeconfig get "$CLUSTER_NAME" > "$KCONFIG"
      # Fix API address for localhost access
      sed -i.bak "s|server: .*|server: https://localhost:6550|" "$KCONFIG"
      rm -f "$KCONFIG.bak"

      # Wait for cluster ready
      log_info "Waiting for cluster to be ready..."
      kubectl --kubeconfig="$KCONFIG" wait --for=condition=Ready nodes --all --timeout=120s

      log_success "k3d cluster provisioned"
      echo ""
      echo "KUBECONFIG=$KCONFIG"
      echo ""
      echo "Next: devenv tasks run k8s:deploy-dask"
    '';

    "k8s:deploy-dask".exec = ''
      source scripts/polaris_bootstrap_helper.sh
      KCONFIG="''${KUBECONFIG:-$PWD/.devenv/state/kubeconfig}"

      if [ ! -f "$KCONFIG" ]; then
        log_error "No kubeconfig found. Run: devenv tasks run k8s:provision"
        exit 1
      fi
      export KUBECONFIG="$KCONFIG"

      log_info "Deploying Dask operator..."
      helm repo add dask https://helm.dask.org || true
      helm repo update dask
      helm upgrade --install dask-operator dask/dask-kubernetes-operator \
        --namespace dask-operator --create-namespace \
        --wait --timeout 5m

      log_info "Waiting for CRDs..."
      kubectl wait --for=condition=Established crd/daskclusters.kubernetes.dask.org --timeout=60s

      log_info "Deploying Dask cluster..."
      kubectl apply -f infra/dask/dask-cluster.yaml

      log_info "Waiting for Dask cluster to be Running..."
      for i in $(seq 1 60); do
        PHASE=$(kubectl get daskcluster -n dask cybersec-dask -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [ "$PHASE" = "Running" ]; then
          log_success "Dask cluster is Running"
          break
        fi
        sleep 5
      done

      echo ""
      echo "Start port-forward: devenv tasks run k8s:forward"
    '';

    "k8s:deploy-jupyter".exec = ''
      source scripts/polaris_bootstrap_helper.sh
      KCONFIG="''${KUBECONFIG:-$PWD/.devenv/state/kubeconfig}"

      if [ ! -f "$KCONFIG" ]; then
        log_error "No kubeconfig found. Run: devenv tasks run k8s:provision"
        exit 1
      fi
      export KUBECONFIG="$KCONFIG"

      log_info "Deploying JupyterHub..."
      helm repo add jupyterhub https://hub.jupyter.org/helm-chart/ || true
      helm repo update jupyterhub
      helm upgrade --install jupyterhub jupyterhub/jupyterhub \
        --namespace jupyterhub --create-namespace \
        --version 2.0.0 \
        --set singleuser.cpu.limit=0.5 \
        --set singleuser.memory.limit=512Mi \
        --wait --timeout 10m

      log_success "JupyterHub deployed"
      echo ""
      echo "Start port-forward: devenv tasks run k8s:forward"
    '';

    "k8s:forward".exec = ''
      KCONFIG="''${KUBECONFIG:-$PWD/.devenv/state/kubeconfig}"

      if [ ! -f "$KCONFIG" ]; then
        echo "No kubeconfig found. Run: devenv tasks run k8s:provision"
        exit 1
      fi
      export KUBECONFIG="$KCONFIG"

      echo "Starting port-forwards (Ctrl+C to stop)..."
      echo "  Dask Dashboard:  http://localhost:8787"
      echo "  JupyterHub:      http://localhost:8000"
      echo ""

      # Run port-forwards in parallel
      kubectl port-forward -n dask svc/cybersec-dask-scheduler 8787:8787 &
      PF1=$!
      kubectl port-forward -n jupyterhub svc/proxy-public 8000:80 &
      PF2=$!

      trap "kill $PF1 $PF2 2>/dev/null" EXIT
      wait
    '';

    "k8s:destroy".exec = ''
      source scripts/polaris_bootstrap_helper.sh

      log_warn "Destroying k3d cluster..."

      CLUSTER_NAME="''${K3D_CLUSTER_NAME:-cybersec}"
      k3d cluster delete "$CLUSTER_NAME" 2>/dev/null || true
      rm -f "$PWD/.devenv/state/kubeconfig"

      log_success "k3d cluster destroyed"
    '';

    "restart:clean".exec = ''
      source scripts/polaris_bootstrap_helper.sh

      # === Auto-bootstrap if needed ===
      FLINK_DIST="thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"

      # Check if git submodules need initialization
      if [ ! -d "thirdparty/flink/.git" ] && [ ! -f "thirdparty/flink/pom.xml" ]; then
        log_info "=== First-time setup: Initializing git submodules ==="
        git submodule update --init --recursive
        log_success "Git submodules initialized"
      fi

      # Check if Flink needs to be built
      if [ ! -f "''${FLINK_DIST}/bin/flink" ]; then
        log_info "=== First-time setup: Building Flink from source ==="
        log_info "This takes 10-15 minutes on first run..."
        cd thirdparty/flink
        mvn clean install -DskipTests -Dfast -T 1C
        cd ../..
        if [ -f "''${FLINK_DIST}/bin/flink" ]; then
          log_success "Flink built successfully"
        else
          log_error "Flink build failed - check Maven output above"
          exit 1
        fi
      fi

      # Check if Polaris needs to be built
      POLARIS_HOME="thirdparty/polaris/polaris-bin-1.3.0-incubating"
      if [ ! -f "''${POLARIS_HOME}/server/quarkus-run.jar" ]; then
        log_info "=== First-time setup: Building Polaris from source ==="
        log_info "This takes 3-5 minutes on first run..."
        cd thirdparty/polaris
        ./gradlew :polaris-distribution:assemble -x test -x integrationTest

        # Extract the distribution
        DIST_TGZ="runtime/distribution/build/distributions/polaris-bin-1.3.0-incubating.tgz"
        if [ -f "$DIST_TGZ" ]; then
          tar -xzf "$DIST_TGZ" -C .
          log_success "Polaris distribution extracted"
        else
          log_error "Polaris build failed - distribution tarball not found"
          exit 1
        fi
        cd ../..

        # Create wrapper scripts
        if [ -f "scripts/setup_polaris_bin.sh" ]; then
          ./scripts/setup_polaris_bin.sh "$POLARIS_HOME"
        fi

        if [ -f "''${POLARIS_HOME}/server/quarkus-run.jar" ]; then
          log_success "Polaris built successfully"
        else
          log_error "Polaris build failed - server JAR not found"
          exit 1
        fi
      fi

      echo "Aggressively stopping all processes..."

      # Portable process killing function (works on both Linux and macOS)
      kill_by_pattern() {
        local pattern="''$1"
        # Use ps + grep for maximum portability (works on Linux and macOS)
        ps aux | grep -E "''$pattern" | grep -v grep | awk '{print ''$2}' | xargs kill -9 2>/dev/null || true
      }

      # Portable port killing function
      kill_by_port() {
        local port="''$1"
        if command -v lsof &> /dev/null; then
          lsof -ti:"''$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
        fi
      }

      # Kill process-compose and all related processes
      kill_by_pattern "process-compose"
      kill_by_pattern "iceberg-browser"
      kill_by_pattern "cloudtrail"
      kill_by_pattern "devenv-tasks"

      # Kill OTEL collector and Prometheus explicitly
      kill_by_pattern "otelcol"
      kill_by_pattern "prometheus"

      # Kill NiFi processes
      kill_by_pattern "org.apache.nifi"

      # Kill by port - ALL services:
      # 8181/8182: Polaris REST/Admin
      # 5438: PostgreSQL
      # 9010/9011: MinIO API/Console
      # 8081: Flink
      # 5050: Iceberg Browser
      # 8450: NiFi
      # 4317/4318: OTEL gRPC/HTTP
      # 8888/8889: OTEL internal/Prometheus metrics
      # 9090: Prometheus
      # 8786/8787: Dask scheduler/dashboard (port-forward)
      # 10443: Kubernetes Dashboard (port-forward)
      # 6550: k3d API server (host port)
      # 8000: JupyterHub (port-forward)
      for port in 8181 8182 5438 9010 9011 8081 5050 8450 4317 4318 8888 8889 9090 8786 8787 10443 6550 8000; do
        kill_by_port "$port"
      done

      # Kill remaining service processes using portable pattern matching
      kill_by_pattern "minio|postgres|flink|taskmanager|jobmanager|quarkus|polaris|otelcol|nifi"

      # Verify critical ports are released
      log_info "Verifying ports are released..."
      for port in 8181 8182 5438 8888 8889 9090; do
        wait_for_port_release $port 5 2 || log_warn "Port $port may still be in use"
      done

      # Clean up socket and temp files (portable path using TMPDIR)
      rm -f "$TMPDIR"/devenv-*/pc.sock 2>/dev/null || true
      rm -f /tmp/devenv-*/pc.sock 2>/dev/null || true
      rm -f /tmp/cloudtrail.log /tmp/cloudtrail.pid /tmp/polaris-init.log /tmp/polaris-catalog-init.log 2>/dev/null || true

      # macOS-specific: restart Podman machine and refresh connection
      if command -v podman >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
        echo "Restarting Podman machine..."
        podman machine stop podman-machine-default >/dev/null 2>&1 || true
        podman machine start podman-machine-default >/dev/null 2>&1 || true
        if PODMAN_CONNS=$(podman system connection list --format json 2>/dev/null); then
          DEFAULT_CONN=$(echo "$PODMAN_CONNS" | jq -r 'map(select(.Default==true)) | .[0].Name // empty')
          if [ -z "$DEFAULT_CONN" ]; then
            FIRST_CONN=$(echo "$PODMAN_CONNS" | jq -r 'map(select(.ReadWrite==true)) | .[0].Name // empty')
            if [ -n "$FIRST_CONN" ]; then
              podman system connection default "$FIRST_CONN" >/dev/null 2>&1 || true
            fi
          fi
        fi
      fi

      # Clean up k3d cluster and kubeconfig artifacts
      if command -v k3d >/dev/null 2>&1; then
        k3d cluster delete cybersec >/dev/null 2>&1 || true
      fi
      rm -f "$DEVENV_STATE/kubeconfig" 2>/dev/null || true
      rm -f "$PWD/build/kubeconfig" 2>/dev/null || true
      rm -f "$PWD/build/kubeconfig-dashboard" 2>/dev/null || true

      # Clean up k3d Podman resources if available
      if command -v podman >/dev/null 2>&1; then
        PODMAN_CMD="podman"
        if [ -n "''${DOCKER_HOST:-}" ] && [ "''${DOCKER_HOST#unix://}" != "$DOCKER_HOST" ]; then
          SOCKET_PATH="''${DOCKER_HOST#unix://}"
          if [ -S "$SOCKET_PATH" ]; then
            PODMAN_CMD="podman --url unix://$SOCKET_PATH"
          fi
        fi
        $PODMAN_CMD rm -f k3d-cybersec-tools >/dev/null 2>&1 || true
        $PODMAN_CMD network rm k3d-cybersec >/dev/null 2>&1 || true
        $PODMAN_CMD volume rm k3d-cybersec-images >/dev/null 2>&1 || true
      fi

      echo "All processes killed and temp files cleaned"
      
      # Remove PostgreSQL data to trigger fresh initialization
      echo "Removing PostgreSQL data for fresh initialization..."
      if [ -d "$DEVENV_STATE/postgres" ]; then
        rm -rf "$DEVENV_STATE/postgres"
        echo "PostgreSQL data removed"
      else
        echo "No PostgreSQL data found to remove"
      fi
      
      echo "Note: Fresh PostgreSQL will initialize with polaris_schema"
      echo "Note: Polaris catalog will be created automatically on startup"
      sleep 2
      
      # Check if bootstrap is needed (missing connectors, etc.)
      echo "Checking bootstrap status..."
      ASSESS_OUTPUT=$(uv run python -c "
import asyncio
from cybersec.bootstrap.service import BootstrapService
async def check():
    svc = BootstrapService()
    result = await svc.assess()
    print('NEEDS_BOOTSTRAP=' + ('1' if result['needs_bootstrap'] else '0'))
    print('ICEBERG_CONNECTOR=' + ('1' if result.get('iceberg_connector_installed') else '0'))
    print('FLINK_INSTALLED=' + ('1' if result['flink_installed'] else '0'))
asyncio.run(check())
" 2>/dev/null || echo "NEEDS_BOOTSTRAP=1")

      if echo "$ASSESS_OUTPUT" | grep -q "NEEDS_BOOTSTRAP=1"; then
        log_warn "Bootstrap required - running bootstrap..."
        if echo "$ASSESS_OUTPUT" | grep -q "ICEBERG_CONNECTOR=0"; then
          log_info "Missing Iceberg connector - will download"
        fi
        # Run bootstrap (non-interactive - will use defaults/skip prompts)
        uv run python -c "
import asyncio
from cybersec.bootstrap.service import BootstrapService
async def run():
    svc = BootstrapService()
    async for event in svc.run(skip_flink=False, skip_nifi=False):
        if event.message:
            print(f'  {event.message}')
asyncio.run(run())
" 2>&1 | while read line; do echo "  $line"; done
        echo "✅ Bootstrap completed"
      else
        echo "✅ Bootstrap already complete"
      fi

      echo "🚀 Starting fresh stack with devenv up -d..."
      devenv up -d &

      # Wait for services and verify complete E2E pipeline
      sleep 10
      log_info "Waiting for services to start and verifying E2E pipeline..."
      if verify_e2e; then
        log_success "Clean restart and E2E verification completed successfully!"
        log_info "✓ All services running"
        log_info "✓ Polaris catalog initialized"
        log_info "✓ CloudTrail DataGen job running"
        log_info "✓ Events flowing to Iceberg Browser"
        exit 0
      else
        log_warn "E2E verification failed - check logs for details"
        log_warn "Continuing despite E2E verification failure"
        exit 0
      fi
    '';
  };


  # ============================================================================
  # OpenTelemetry Collector - Receives telemetry from all Gaius components
  # ============================================================================
  services.opentelemetry-collector = {
    enable = true;
    package = pkgs.opentelemetry-collector-contrib;  # Use contrib for prometheus exporter
    settings = {
      receivers = {
        otlp = {
          protocols = {
            grpc.endpoint = "0.0.0.0:4317";
            http.endpoint = "0.0.0.0:4318";
          };
        };
      };
      processors = {
        batch = {
          timeout = "5s";
          send_batch_size = 1000;
        };
      };
      exporters = {
        prometheus = {
          endpoint = "0.0.0.0:8889";
          namespace = "cybersec";
          resource_to_telemetry_conversion.enabled = true;
        };
        debug.verbosity = "basic";
        # Forward traces to NiFi ListenOTLP for flow visualization
        # NiFi receives OTel data on port 4319 via ListenOTLP processor
        otlphttp = {
          endpoint = "http://localhost:4319";
          tls.insecure = true;
        };
      };
      service = {
        pipelines = {
          traces = {
            receivers = ["otlp"];
            processors = ["batch"];
            exporters = ["debug" "otlphttp"];  # Forward to NiFi
          };
          metrics = {
            receivers = ["otlp"];
            processors = ["batch"];
            exporters = ["prometheus"];
          };
        };
      };
    };
  };

  # ============================================================================
  # Prometheus - Metrics storage and querying
  # ============================================================================
  services.prometheus = {
    enable = true;
    port = 9090;
    # Note: Prometheus binds to 0.0.0.0 by default when port is specified
    storage.retentionTime = "15d";
    scrapeConfigs = [
      {
        job_name = "otel-collector";
        scrape_interval = "1s";  # 1s scraping for real-time ObservePanel
        static_configs = [{
          targets = ["localhost:8889"];
        }];
      }
      {
        job_name = "cost-monitor";
        scrape_interval = "60s";  # 1-minute granularity for cost metrics
        static_configs = [{
          targets = ["localhost:9876"];
        }];
      }
    ];
  };
  # Process-compose managed services (Flink, Polaris, Dask, auxiliary tooling)
  processes = {
    podman-runtime = {
      exec = ''
        set -euo pipefail

        # Podman is only needed for k3d provisioning (not for RKE2)
        if [ "''${CYBERSEC_K8S_TARGET:-none}" != "k3d" ]; then
          echo "K8s target is ''${CYBERSEC_K8S_TARGET:-none}, not k3d - skipping podman"
          exit 0
        fi

        if ! command -v podman >/dev/null 2>&1; then
          echo "podman CLI not found in dev environment"
          exit 1
        fi

        echo "Ensuring Podman environment is available for k3d..."

        if podman machine list >/dev/null 2>&1; then
          MACHINE_JSON=$(podman machine list --format=json 2>/dev/null || echo "[]")
          MACHINE_NAME=$(echo "$MACHINE_JSON" | jq -r 'map(select(.Name != null)) | map(.Name)[0] // empty')
          if [ -n "$MACHINE_NAME" ]; then
            MACHINE_STATE=$(echo "$MACHINE_JSON" | jq -r --arg name "$MACHINE_NAME" '.[] | select(.Name==$name) | .State // ""')
            MACHINE_STATE=$(echo "$MACHINE_STATE" | tr '[:upper:]' '[:lower:]')
            if [ "$MACHINE_STATE" != "running" ]; then
              echo "Starting Podman machine '$MACHINE_NAME'..."
              podman machine start "$MACHINE_NAME" || true
            else
              echo "Podman machine '$MACHINE_NAME' already running"
            fi
          else
            echo "No Podman machines defined; assuming native runtime"
          fi
        else
          echo "Podman machine tooling unavailable (likely native Linux runtime)"
        fi

        while true; do
          sleep 300
        done
      '';
      process-compose = {
        disabled = true;  # Started via k8s:provision task
        availability = {
          restart = "always";
        };
      };
    };

    k3d-cluster = {
      exec = ''
        set -euo pipefail

        # k3d cluster provisioning - only for local k3d target (not RKE2)
        if [ "''${CYBERSEC_K8S_TARGET:-none}" != "k3d" ]; then
          echo "K8s target is ''${CYBERSEC_K8S_TARGET:-none}, not k3d - skipping k3d cluster provisioning"
          exit 0
        fi

        CLUSTER_NAME=''${K3D_CLUSTER_NAME:-cybersec}
        KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"

        export K3D_FIX_DNS=0

        if ! command -v k3d >/dev/null 2>&1; then
          echo "k3d CLI not found"
          exit 1
        fi

        if ! command -v kubectl >/dev/null 2>&1; then
          echo "kubectl CLI not found"
          exit 1
        fi

        if command -v podman >/dev/null 2>&1 && podman machine list >/dev/null 2>&1; then
          MACHINE_JSON=$(podman machine list --format=json 2>/dev/null || echo "[]")
          MACHINE_NAME=$(echo "$MACHINE_JSON" | jq -r 'map(select(.Name != null)) | map(.Name)[0] // empty')
          CONN_URI=""
          if CONN_JSON=$(podman system connection list --format json 2>/dev/null); then
            CONN_URI=$(echo "$CONN_JSON" | jq -r 'map(select(.Default==true)) | .[0].URI // empty')
            if [ -z "$CONN_URI" ] && [ -n "$MACHINE_NAME" ]; then
              CONN_URI=$(echo "$CONN_JSON" | jq -r --arg name "$MACHINE_NAME" 'map(select(.Name==$name)) | .[0].URI // empty')
            fi
          fi
          if [ -n "$CONN_URI" ]; then
            export DOCKER_HOST="$CONN_URI"
            export K3D_HIDE_WARNING_ROOTLESS=1
            echo "Using Podman connection $CONN_URI for k3d"
          elif [ -n "$MACHINE_NAME" ]; then
            SOCKET_PATH=$(podman machine inspect "$MACHINE_NAME" 2>/dev/null | jq -r '.[0].ConnectionInfo.PodmanSocket.Path // empty')
            if [ -n "$SOCKET_PATH" ] && [ -S "$SOCKET_PATH" ]; then
              export DOCKER_HOST="unix://$SOCKET_PATH"
              export K3D_HIDE_WARNING_ROOTLESS=1
              echo "Using Podman machine socket at $SOCKET_PATH for k3d"
            fi
          fi
        fi

        mkdir -p "$(dirname "$KUBECONFIG_PATH")"

        if command -v podman >/dev/null 2>&1 && [ -n "''${DOCKER_HOST:-}" ]; then
          PODMAN_CMD="podman"
          case "''${DOCKER_HOST}" in
            unix://*)
              SOCKET_PATH="''${DOCKER_HOST#unix://}"
              if [ -S "$SOCKET_PATH" ]; then
                PODMAN_CMD="podman --url unix://$SOCKET_PATH"
              fi
              ;;
            ssh://*|tcp://*)
              PODMAN_CMD="podman --url ''${DOCKER_HOST}"
              ;;
          esac
          NETWORK_NAME="k3d-$CLUSTER_NAME"
          IMAGE_VOLUME="k3d-$CLUSTER_NAME-images"
          TOOLS_NODE="k3d-$CLUSTER_NAME-tools"

            K3D_VERSION=""
            if K3D_VERSION_JSON=$(k3d version --output json 2>/dev/null); then
              case "$K3D_VERSION_JSON" in
                \{*\}|\[*\])
                  K3D_VERSION=$(echo "$K3D_VERSION_JSON" | jq -r '(.k3d.version? // .k3d? // empty)' | sed 's/^v//')
                  ;;
              esac
            fi
            if [ -z "$K3D_VERSION" ]; then
              K3D_VERSION=$(k3d version 2>/dev/null | awk '/k3d version/ {print $3}' | sed 's/^v//')
            fi
            TOOLS_IMAGE="''${K3D_IMAGE_TOOLS:-}"
            if [ -z "$TOOLS_IMAGE" ]; then
              if [ -n "''${K3D_HELPER_IMAGE_TAG:-}" ]; then
                TOOLS_IMAGE="ghcr.io/k3d-io/k3d-tools:''${K3D_HELPER_IMAGE_TAG}"
              elif [ -n "$K3D_VERSION" ]; then
                TOOLS_IMAGE="ghcr.io/k3d-io/k3d-tools:$K3D_VERSION"
              else
                TOOLS_IMAGE="ghcr.io/k3d-io/k3d-tools:latest"
              fi
            fi

          if ! $PODMAN_CMD network exists "$NETWORK_NAME" >/dev/null 2>&1; then
            $PODMAN_CMD network create "$NETWORK_NAME" >/dev/null
          fi

          if ! $PODMAN_CMD volume exists "$IMAGE_VOLUME" >/dev/null 2>&1; then
            $PODMAN_CMD volume create "$IMAGE_VOLUME" >/dev/null
          fi

          if $PODMAN_CMD container exists "$TOOLS_NODE" >/dev/null 2>&1; then
            EXISTING_LABEL=$($PODMAN_CMD inspect "$TOOLS_NODE" --format '{{index .Config.Labels "app"}}' 2>/dev/null || true)
            if [ "$EXISTING_LABEL" != "k3d" ]; then
              $PODMAN_CMD rm -f "$TOOLS_NODE" >/dev/null 2>&1 || true
            fi
          fi

          if ! $PODMAN_CMD container exists "$TOOLS_NODE" >/dev/null 2>&1; then
            $PODMAN_CMD run -d \
              --name "$TOOLS_NODE" \
              --label app=k3d \
              --network "$NETWORK_NAME" \
              -v "$IMAGE_VOLUME:/k3d/images" \
              "$TOOLS_IMAGE" noop >/dev/null
          fi
        fi

        CLUSTER_EXISTS=0
        if k3d cluster list | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER_NAME"; then
          CLUSTER_EXISTS=1
        fi

        if [ "$CLUSTER_EXISTS" -eq 1 ]; then
          if k3d node list | awk 'NR>1 {print $1}' | grep -q "^k3d-$CLUSTER_NAME-serverlb$"; then
            echo "Existing cluster has serverlb; recreating to apply disabled load balancer..."
            k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
            CLUSTER_EXISTS=0
          fi
        fi

        if [ "$CLUSTER_EXISTS" -eq 1 ] && command -v lsof >/dev/null 2>&1; then
          if lsof -nP -iTCP:8786 -sTCP:LISTEN 2>/dev/null | grep -q "gvproxy" || \
             lsof -nP -iTCP:8787 -sTCP:LISTEN 2>/dev/null | grep -q "gvproxy"; then
            echo "Existing cluster is binding Dask ports via gvproxy; recreating to remove host port mappings..."
            k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
            CLUSTER_EXISTS=0
          fi
        fi

        if [ "$CLUSTER_EXISTS" -eq 0 ]; then
          echo "Creating k3d cluster '$CLUSTER_NAME'..."

          CONFIG_FILE=$(mktemp)
          cat > "$CONFIG_FILE" <<EOF
apiVersion: k3d.io/v1alpha5
kind: Simple
metadata:
  name: $CLUSTER_NAME
image: rancher/k3s:v1.31.6-k3s1
servers: 1
agents: 0
options:
  k3d:
    wait: true
    timeout: 300s
    disableLoadbalancer: true
  k3s:
    extraArgs:
      - arg: --disable=traefik
        nodeFilters:
          - server:*
      - arg: --disable=servicelb
        nodeFilters:
          - server:*
      - arg: --disable=traefik
        nodeFilters:
          - agent:*
      - arg: --disable=servicelb
        nodeFilters:
          - agent:*
EOF

          trap 'rm -f "$CONFIG_FILE"' EXIT
          k3d cluster create --api-port 0.0.0.0:6550 --config "$CONFIG_FILE"
          rm -f "$CONFIG_FILE"
          trap - EXIT
        else
          echo "k3d cluster '$CLUSTER_NAME' already exists; ensuring it is started..."
          k3d cluster start "$CLUSTER_NAME" || true
        fi

        TMP_KUBECONFIG="$KUBECONFIG_PATH.tmp"
        k3d kubeconfig get "$CLUSTER_NAME" > "$TMP_KUBECONFIG"
        mv "$TMP_KUBECONFIG" "$KUBECONFIG_PATH"
        chmod 600 "$KUBECONFIG_PATH"
        ln -sfn "$PWD/.devenv/state" "$PWD/kube-state"
        ln -sfn "$KUBECONFIG_PATH" "$PWD/kubeconfig"
        python -c "from pathlib import Path; path = Path(\"''${KUBECONFIG_PATH}\"); text = path.read_text(); text = text.replace('https://0.0.0.0:', 'https://127.0.0.1:'); text = text.replace('https://host.k3d.internal:', 'https://127.0.0.1:'); path.write_text(text)"
        export KUBECONFIG="$KUBECONFIG_PATH"

        echo "Waiting for Kubernetes API..."
        for _ in $(seq 1 60); do
          if kubectl get nodes >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        kubectl wait --for=condition=Ready node --all --timeout=300s

        echo "k3d cluster '$CLUSTER_NAME' is ready"

        while true; do
          if ! kubectl get nodes >/dev/null 2>&1; then
            echo "Lost connection to cluster; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
        disabled = true;  # Started via k8s:provision task
        depends_on = {
          podman-runtime = {
            condition = "process_started";
          };
        };
      };
    };

    dask-operator = {
      exec = ''
        set -euo pipefail

        # Dask operator runs on any K8s target (k3d or RKE2)
        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "Waiting for kubeconfig at $KUBECONFIG_PATH..."
          for _ in $(seq 1 60); do
            if [ -f "$KUBECONFIG_PATH" ]; then
              break
            fi
            sleep 2
          done
        fi

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "kubeconfig not found at $KUBECONFIG_PATH"
          exit 1
        fi

        echo "Waiting for Kubernetes control plane before installing Dask operator..."
        for _ in $(seq 1 60); do
          if kubectl get namespace kube-system >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        helm repo add dask https://helm.dask.org >/dev/null 2>&1 || true
        helm repo update dask >/dev/null 2>&1 || true

        echo "Installing/Updating Dask Kubernetes operator via Helm..."
        helm upgrade --install dask-operator dask/dask-kubernetes-operator \
          --namespace dask-operator \
          --create-namespace \
          --wait \
          --timeout 5m

        kubectl wait --for=condition=Established crd/daskclusters.kubernetes.dask.org --timeout=120s
        kubectl wait --for=condition=Established crd/daskworkergroups.kubernetes.dask.org --timeout=120s

        echo "Dask operator installed"

        while true; do
          if ! kubectl get pods -n dask-operator >/dev/null 2>&1; then
            echo "Unable to query Dask operator pods; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
        disabled = true;  # Started via k8s:deploy-dask task
        depends_on = {
          k3d-cluster = {
            condition = "process_started";
          };
        };
      };
    };

    yunikorn = {
      exec = ''
        set -euo pipefail

        if [ "''${ENABLE_YUNIKORN:-false}" != "true" ]; then
          echo "YuniKorn disabled (set ENABLE_YUNIKORN=true to enable)"
          exit 0
        fi

        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "Waiting for kubeconfig at $KUBECONFIG_PATH..."
          for _ in $(seq 1 60); do
            if [ -f "$KUBECONFIG_PATH" ]; then
              break
            fi
            sleep 2
          done
        fi

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "kubeconfig not found at $KUBECONFIG_PATH"
          exit 1
        fi

        echo "Waiting for Kubernetes control plane before installing YuniKorn..."
        for _ in $(seq 1 60); do
          if kubectl get namespace kube-system >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        helm repo add yunikorn https://apache.github.io/yunikorn-release >/dev/null 2>&1 || true
        helm repo update yunikorn >/dev/null 2>&1 || true

        echo "Installing/Updating YuniKorn scheduler via Helm..."
        helm upgrade --install yunikorn yunikorn/yunikorn \
          --namespace yunikorn \
          --create-namespace \
          --version 1.8.0 \
          --wait \
          --timeout 5m

        kubectl wait --for=condition=Available deployment/yunikorn-scheduler -n yunikorn --timeout=120s

        echo "YuniKorn scheduler installed"

        while true; do
          if ! kubectl get pods -n yunikorn >/dev/null 2>&1; then
            echo "Unable to query YuniKorn pods; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
        disabled = true;  # Optional, enabled via ENABLE_YUNIKORN
        depends_on = {
          dask-operator = {
            condition = "process_started";
          };
        };
      };
    };

    dask-cluster = {
      exec = ''
        set -euo pipefail

        # Dask cluster runs on any K8s target (k3d or RKE2)
        if [ "''${ENABLE_YUNIKORN:-false}" = "true" ]; then
          MANIFEST="$PWD/infra/dask/dask-cluster-yunikorn.yaml"
        else
          MANIFEST="$PWD/infra/dask/dask-cluster.yaml"
        fi
        if [ ! -f "$MANIFEST" ]; then
          echo "Dask manifest not found at $MANIFEST"
          exit 1
        fi

        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        echo "Waiting for Dask operator CRDs..."
        for _ in $(seq 1 60); do
          if kubectl get crd daskclusters.kubernetes.dask.org >/dev/null 2>&1 && \
             kubectl get crd daskworkergroups.kubernetes.dask.org >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        kubectl apply -f "$MANIFEST"

        echo "Waiting for Dask cluster to reach Running phase..."
        STATUS=""
        for _ in $(seq 1 120); do
          STATUS=$(kubectl get daskcluster cybersec-dask -n dask -o jsonpath='{.status.phase}' 2>/dev/null || true)
          if [ "$STATUS" = "Running" ]; then
            break
          fi
          sleep 5
        done

        if [ "$STATUS" != "Running" ]; then
          echo "Dask cluster did not reach Running status (last status: ''${STATUS:-unknown})"
          kubectl get daskclusters -n dask || true
          exit 1
        fi

        echo "Dask cluster is running"
        kubectl get svc -n dask cybersec-dask-scheduler || true

        while true; do
          CURRENT=$(kubectl get daskcluster cybersec-dask -n dask -o jsonpath='{.status.phase}' 2>/dev/null || true)
          if [ "$CURRENT" != "Running" ]; then
            echo "Dask cluster status is '$CURRENT'; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
        disabled = true;  # Started via k8s:deploy-dask task
        depends_on = {
          dask-operator = {
            condition = "process_started";
          };
        };
      };
    };

    k8s-dashboard = {
      exec = ''
        set -euo pipefail

        # K8s dashboard runs on any K8s target (k3d or RKE2)
        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        echo "Waiting for Kubernetes API..."
        for _ in $(seq 1 60); do
          if kubectl get namespace kube-system >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        echo "Installing Kubernetes Dashboard..."
        kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.7.0/aio/deploy/recommended.yaml

        cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: admin-user
  namespace: kubernetes-dashboard
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-user
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: admin-user
    namespace: kubernetes-dashboard
EOF

        kubectl -n kubernetes-dashboard rollout status deploy/kubernetes-dashboard --timeout=300s || true

        if kubectl -n kubernetes-dashboard get sa admin-user >/dev/null 2>&1; then
          TOKEN=$(kubectl -n kubernetes-dashboard create token admin-user 2>/dev/null || true)
          if [ -n "$TOKEN" ]; then
            echo "Kubernetes Dashboard token: $TOKEN"
            CA_DATA=$(awk '/certificate-authority-data:/ {print $2; exit}' "$KUBECONFIG_PATH")
            SERVER=$(awk '/server:/ {print $2; exit}' "$KUBECONFIG_PATH")
            if [ -n "$CA_DATA" ] && [ -n "$SERVER" ]; then
              mkdir -p "$PWD/build"
              cat > "$PWD/build/kubeconfig-dashboard" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: k3d-cybersec
  cluster:
    certificate-authority-data: $CA_DATA
    server: $SERVER
contexts:
- name: k3d-cybersec
  context:
    cluster: k3d-cybersec
    user: dashboard-admin
current-context: k3d-cybersec
users:
- name: dashboard-admin
  user:
    token: $TOKEN
EOF
              chmod 600 "$PWD/build/kubeconfig-dashboard"
              ln -sfn "$PWD/build/kubeconfig-dashboard" "$PWD/kubeconfig-dashboard"
            fi
          fi
        fi

        echo "Starting Kubernetes Dashboard on https://localhost:10443 ..."
        kubectl -n kubernetes-dashboard port-forward svc/kubernetes-dashboard 10443:443 --address 127.0.0.1,::1
      '';
      process-compose = {
        disabled = true;  # Started via k8s:forward task
        depends_on = {
          k3d-cluster = {
            condition = "process_started";
          };
        };
      };
    };

    jupyterhub = {
      exec = ''
        set -euo pipefail

        # JupyterHub runs on any K8s target (k3d or RKE2)
        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "Waiting for kubeconfig at $KUBECONFIG_PATH..."
          for _ in $(seq 1 60); do
            if [ -f "$KUBECONFIG_PATH" ]; then
              break
            fi
            sleep 2
          done
        fi

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "kubeconfig not found at $KUBECONFIG_PATH"
          exit 1
        fi

        echo "Waiting for Kubernetes API..."
        for _ in $(seq 1 60); do
          if kubectl get namespace kube-system >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        echo "Installing/Updating JupyterHub via Helm..."
        helm repo add jupyterhub https://jupyterhub.github.io/helm-chart/ >/dev/null 2>&1 || true
        helm repo update jupyterhub >/dev/null 2>&1 || true
        NOTEBOOK_PATH="$PWD/build/Dask_Kub_Viz_Sample_Problem.ipynb"
        EXTRA_ARGS=()
        if [ -f "$NOTEBOOK_PATH" ]; then
          EXTRA_ARGS+=(--set-file "singleuser.extraFiles.dask_notebook.stringData=$NOTEBOOK_PATH")
          EXTRA_ARGS+=(--set "singleuser.extraFiles.dask_notebook.mountPath=/home/jovyan/Dask_Kub_Viz_Sample_Problem.ipynb")
          EXTRA_ARGS+=(--set "singleuser.extraFiles.dask_notebook.mode=420")
        fi
        EXTRA_ARGS+=(--set-json "singleuser.lifecycleHooks.postStart.exec.command=[\"/bin/sh\",\"-c\",\"pip install --quiet holoviews datashader bokeh dask[distributed] pyarrow\"]")
        helm upgrade --install jupyterhub jupyterhub/jupyterhub \
          --version 2.0.0 \
          --namespace jupyterhub \
          --create-namespace \
          --set-json singleuser.cpu.guarantee=0.1 \
          --set-json singleuser.cpu.limit=0.5 \
          --set-string singleuser.memory.guarantee=256M \
          --set-string singleuser.memory.limit=512M \
          --set singleuser.networkPolicy.enabled=false \
          "''${EXTRA_ARGS[@]}"

        kubectl -n jupyterhub rollout status deploy/hub --timeout=300s || true
        kubectl -n jupyterhub rollout status deploy/proxy --timeout=300s || true

        echo "Starting JupyterHub on http://localhost:8000 ..."
        kubectl -n jupyterhub port-forward svc/proxy-public 8000:80 --address 127.0.0.1,::1
      '';
      process-compose = {
        disabled = true;  # Started via k8s:deploy-jupyter task
        depends_on = {
          k3d-cluster = {
            condition = "process_started";
          };
        };
      };
    };

    dask-ui = {
      exec = ''
        set -euo pipefail

        # Dask UI runs on any K8s target (k3d or RKE2)
        # Use existing KUBECONFIG if set, otherwise fall back to k3d-generated config
        if [ -n "''${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ]; then
          KUBECONFIG_PATH="$KUBECONFIG"
        else
          KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        fi
        export KUBECONFIG="$KUBECONFIG_PATH"

        echo "Waiting for Dask scheduler pod..."
        for _ in $(seq 1 120); do
          SCHEDULER_POD=$(kubectl -n dask get pods -l dask.org/component=scheduler -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
          if [ -n "$SCHEDULER_POD" ]; then
            READY=$(kubectl -n dask get pod "$SCHEDULER_POD" -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || true)
            if [ "$READY" = "true" ]; then
              break
            fi
          fi
          sleep 2
        done

        if [ -z "''${SCHEDULER_POD:-}" ]; then
          echo "Dask scheduler pod not found"
          exit 1
        fi

        echo "Starting Dask UI on http://localhost:8787 ..."
        kubectl -n dask port-forward pod/$SCHEDULER_POD 8787:8787 --address 127.0.0.1,::1
      '';
      process-compose = {
        disabled = true;  # Started via k8s:forward task
        depends_on = {
          dask-cluster = {
            condition = "process_started";
          };
        };
      };
    };

    # Bootstrap check - runs on startup to verify environment
    bootstrap-check = {
      exec = ''
        echo "======================================================"
        echo "  Cybersec Bootstrap Check"
        echo "======================================================"

        # Quick assessment using the bootstrap CLI
        if command -v python &> /dev/null; then
          python -c "
import sys
sys.path.insert(0, '.')
try:
    import asyncio
    from cybersec.bootstrap import BootstrapService
    service = BootstrapService()
    result = asyncio.run(service.assess())

    print()
    if result.get('config_exists'):
        print('  Config:     OK (.cybersec/config.toml exists)')
    else:
        print('  Config:     Missing (.cybersec/config.toml)')

    flink = result.get('flink_installed', False)
    flink_home = result.get('flink_home')
    if flink and flink_home:
        print(f'  Flink:      OK ({flink_home})')
    else:
        print('  Flink:      Not configured')

    tools = result.get('tools', {})
    missing = [t for t, found in tools.items() if not found]
    if missing:
        print(f'  Tools:      Missing: {missing}')
    else:
        print('  Tools:      OK (all required tools found)')

    print()
    if result.get('ready'):
        print('  Status: Environment is READY')
    elif result.get('needs_bootstrap'):
        print('  Status: Bootstrap REQUIRED')
        print()
        print('  Next steps:')
        print('    1. Open http://localhost:5050/settings in your browser')
        print('    2. Or run: cybersec bootstrap run')
        print('    3. Or run: python -m cybersec.cli.main bootstrap run')
    print()
except ImportError as e:
    print(f'  Bootstrap module not installed: {e}')
    print('  Run: uv pip install -e .')
    print()
except Exception as e:
    print(f'  Error during assessment: {e}')
    print()
"
        else
          echo "  Python not found - skipping bootstrap check"
        fi

        echo "======================================================"
        # One-shot process - exits after check
        exit 0
      '';
      process-compose = {
        availability = {
          restart = "no";
        };
      };
    };

    # Build Flink and install connectors if needed (one-shot process)
    flink-bootstrap = {
      exec = ''
        FLINK_VERSION="1.20.1"
        FLINK_DIST="$PWD/thirdparty/flink/flink-dist/target/flink-''${FLINK_VERSION}-bin/flink-''${FLINK_VERSION}"

        # Check if Flink is already built
        if [ -x "$FLINK_DIST/bin/flink" ]; then
          echo "Flink $FLINK_VERSION already built"
        else
          echo "Building Flink $FLINK_VERSION from source (this takes 10-15 minutes)..."

          # Ensure submodules are initialized
          if [ ! -f "thirdparty/flink/pom.xml" ]; then
            echo "Initializing git submodules..."
            git submodule update --init --recursive
          fi

          cd thirdparty/flink
          mvn clean install -DskipTests -Dfast -T 1C
          cd ../..

          if [ -x "$FLINK_DIST/bin/flink" ]; then
            echo "Flink built successfully"
          else
            echo "Flink build failed"
            exit 1
          fi
        fi

        # Install Iceberg connectors if needed
        ICEBERG_JAR=$(ls "$FLINK_DIST/lib/iceberg-flink-runtime-1.20-"*.jar 2>/dev/null | head -1)
        AWS_BUNDLE_JAR=$(ls "$FLINK_DIST/lib/iceberg-aws-bundle-"*.jar 2>/dev/null | head -1)
        if [ -z "$ICEBERG_JAR" ] || [ -z "$AWS_BUNDLE_JAR" ]; then
          echo "🔧 Installing Iceberg connectors..."

          # Ensure Iceberg submodule is initialized
          if [ ! -f "thirdparty/iceberg/gradlew" ]; then
            echo "  Initializing Iceberg submodule..."
            git submodule update --init thirdparty/iceberg
          fi

          # Build and install Iceberg JARs
          if [ -f "thirdparty/iceberg/gradlew" ]; then
            echo "  Building Iceberg Flink runtime and AWS bundle..."
            cd thirdparty/iceberg
            ./gradlew -PflinkVersions=1.20 \
              :iceberg-flink:iceberg-flink-runtime-1.20:shadowJar \
              :iceberg-aws-bundle:shadowJar \
              -x test -x integrationTest -x generateGitProperties \
              --no-daemon 2>&1 | grep -E "(BUILD|Task|WARN|ERROR)" || true

            # Copy Flink runtime JAR
            for jar in flink/v1.20/flink-runtime/build/libs/iceberg-flink-runtime-1.20-*.jar; do
              if [ -f "$jar" ] && [[ "$jar" != *"-sources.jar" ]] && [[ "$jar" != *"-javadoc.jar" ]]; then
                cp "$jar" "$FLINK_DIST/lib/"
                echo "Installed: $(basename $jar)"
                break
              fi
            done

            # Copy AWS bundle JAR
            for jar in aws-bundle/build/libs/iceberg-aws-bundle-*.jar; do
              if [ -f "$jar" ] && [[ "$jar" != *"-sources.jar" ]] && [[ "$jar" != *"-javadoc.jar" ]]; then
                cp "$jar" "$FLINK_DIST/lib/"
                echo "Installed: $(basename $jar)"
                break
              fi
            done

            cd ../..
          else
            echo "Iceberg submodule not available - run: git submodule update --init thirdparty/iceberg"
          fi

          # Verify installation
          ICEBERG_JAR=$(ls "$FLINK_DIST/lib/iceberg-flink-runtime-1.20-"*.jar 2>/dev/null | head -1)
          AWS_BUNDLE_JAR=$(ls "$FLINK_DIST/lib/iceberg-aws-bundle-"*.jar 2>/dev/null | head -1)
          if [ -z "$ICEBERG_JAR" ] || [ -z "$AWS_BUNDLE_JAR" ]; then
            echo "Iceberg JAR installation failed"
            echo "Missing: iceberg-flink-runtime and/or iceberg-aws-bundle"
            echo "Run: /health fix --apply"
            exit 1
          fi
        else
          echo "Iceberg connectors already installed"
        fi

        # Install S3 filesystem plugin (must be in plugins/, NOT lib/, to avoid delegation token conflict)
        S3_PLUGIN_DIR="$FLINK_DIST/plugins/s3-fs-hadoop"
        if [ ! -f "$S3_PLUGIN_DIR/flink-s3-fs-hadoop-1.20.1.jar" ]; then
          if [ -f "$FLINK_DIST/opt/flink-s3-fs-hadoop-1.20.1.jar" ]; then
            mkdir -p "$S3_PLUGIN_DIR"
            cp "$FLINK_DIST/opt/flink-s3-fs-hadoop-1.20.1.jar" "$S3_PLUGIN_DIR/"
            echo "Installed flink-s3-fs-hadoop as plugin (S3 filesystem support)"
          fi
        fi
        # Remove from lib/ if previously installed there (causes delegation token conflict)
        rm -f "$FLINK_DIST/lib/flink-s3-fs-hadoop-1.20.1.jar" 2>/dev/null || true

        if [ ! -f "$FLINK_DIST/lib/flink-python-1.20.1.jar" ]; then
          if [ -f "$FLINK_DIST/opt/flink-python-1.20.1.jar" ]; then
            cp "$FLINK_DIST/opt/flink-python-1.20.1.jar" "$FLINK_DIST/lib/"
            echo "Copied flink-python to lib (PyFlink support)"
          fi
        fi

        # Copy hadoop-common from Gradle cache (needed by Iceberg FlinkCatalogFactory)
        # This is safe now that flink-s3-fs-hadoop is in plugins/ with classloader isolation
        GRADLE_CACHE="$HOME/.gradle/caches/modules-2/files-2.1"
        if [ -d "$GRADLE_CACHE" ]; then
          HADOOP_COMMON_DIR="$GRADLE_CACHE/org.apache.hadoop/hadoop-common/3.4.1"
          if [ -d "$HADOOP_COMMON_DIR" ]; then
            for jar in "$HADOOP_COMMON_DIR"/*/hadoop-common-3.4.1.jar; do
              if [ -f "$jar" ] && [ ! -f "$FLINK_DIST/lib/hadoop-common-3.4.1.jar" ]; then
                cp "$jar" "$FLINK_DIST/lib/"
                echo "Copied hadoop-common from Gradle cache (for Iceberg catalog)"
                break
              fi
            done
          fi
        fi

        # Clean up conflicting S3/Hadoop JARs that duplicate flink-s3-fs-hadoop's bundled classes
        # NOTE: hadoop-common is kept - only conflicts with S3 delegation tokens (now isolated in plugin)
        CONFLICTING_JARS=(
          "aws-java-sdk-bundle-*.jar"
          "hadoop-auth-*.jar"
          "hadoop-aws-*.jar"
          "hadoop-shaded-guava-*.jar"
        )
        for pattern in "''${CONFLICTING_JARS[@]}"; do
          for jar in "$FLINK_DIST/lib/"$pattern; do
            if [ -f "$jar" ]; then
              rm -f "$jar"
              echo "Removed conflicting JAR: $(basename $jar)"
            fi
          done
        done

        echo "Flink bootstrap complete"
        exit 0
      '';
      process-compose = {
        availability = {
          restart = "no";
        };
      };
    };

    # Build flink-cyber Java datagen JAR if not already built (one-shot process)
    java-datagen-bootstrap = {
      exec = ''
        JAR="$PWD/flink-cyber/flink-common/target/flink-common-2.4.0-iceberg.jar"
        if [ -f "$JAR" ]; then
          echo "Java datagen JAR exists: $JAR"
          exit 0
        fi
        echo "Building flink-cyber Java datagen..."
        cd flink-cyber && mvn clean install -DskipTests -pl flink-common -am
        if [ -f "$JAR" ]; then
          echo "Java datagen JAR built successfully"
          exit 0
        else
          echo "Failed to build Java datagen JAR"
          exit 1
        fi
      '';
      process-compose = {
        availability = {
          restart = "no";
        };
        depends_on = {
          flink-bootstrap = {
            condition = "process_completed_successfully";
          };
        };
      };
    };

    flink-jobmanager = {
      exec = ''
        # Use custom-built Apache Flink 1.20.1 (for Iceberg compatibility)
        export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
        export FLINK_STATE_DIR="$DEVENV_STATE/flink"
        export HADOOP_CONF_DIR="$FLINK_HOME/conf"
        mkdir -p "$FLINK_STATE_DIR"/{logs,checkpoints,savepoints}

        # Verify Flink exists
        if [ ! -x "$FLINK_HOME/bin/jobmanager.sh" ]; then
          echo "Flink not found at $FLINK_HOME"
          echo "Run: devenv tasks run restart:clean"
          exit 1
        fi

        # Add comprehensive Java module opens for checkpoint serialization
        export FLINK_ENV_JAVA_OPTS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED --add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED --add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/sun.security.action=ALL-UNNAMED"

        # Run JobManager in foreground mode
        # classloader.parent-first-patterns: Fix Dropwizard metrics classloader conflict with Iceberg
        exec "$FLINK_HOME/bin/jobmanager.sh" start-foreground \
          -D jobmanager.rpc.address=localhost \
          -D rest.bind-address=0.0.0.0 \
          -D rest.port=8081 \
          -D state.checkpoints.dir=file://$FLINK_STATE_DIR/checkpoints \
          -D state.savepoints.dir=file://$FLINK_STATE_DIR/savepoints \
          -D 'classloader.parent-first-patterns.additional=com.codahale.metrics;org.apache.flink.dropwizard'
      '';
      process-compose = {
        depends_on = {
          flink-bootstrap = {
            condition = "process_completed_successfully";
          };
        };
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 8081;
            path = "/overview";
          };
          initial_delay_seconds = 5;
          period_seconds = 2;
          failure_threshold = 30;
        };
      };
    };

    flink-taskmanager = {
      exec = ''
        # Use custom-built Apache Flink 1.20.1 (for Iceberg compatibility)
        export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
        export FLINK_STATE_DIR="$DEVENV_STATE/flink"
        mkdir -p "$FLINK_STATE_DIR"/{logs,tmp}
        
        # Configure S3A for MinIO
        export HADOOP_CONF_DIR="$FLINK_HOME/conf"
        
        # Add comprehensive Java module opens for checkpoint serialization
        export FLINK_ENV_JAVA_OPTS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED --add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED --add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/sun.security.action=ALL-UNNAMED"
        
        # Run TaskManager in foreground mode
        # classloader.parent-first-patterns: Fix Dropwizard metrics classloader conflict with Iceberg
        exec "$FLINK_HOME/bin/taskmanager.sh" start-foreground \
          -D jobmanager.rpc.address=localhost \
          -D taskmanager.numberOfTaskSlots=4 \
          -D taskmanager.tmp.dirs=$FLINK_STATE_DIR/tmp \
          -D 'classloader.parent-first-patterns.additional=com.codahale.metrics;org.apache.flink.dropwizard'
      '';
      process-compose = {
        depends_on = {
          flink-jobmanager = {
            condition = "process_healthy";
          };
        };
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 8081;
            path = "/taskmanagers";
          };
          initial_delay_seconds = 10;
          period_seconds = 3;
          failure_threshold = 20;
        };
      };
    };

    iceberg-browser = {
      exec = ''
        # Wait for required services
        echo "Starting Iceberg Browser..."
        echo "Web UI will be available at http://localhost:5050"
        
        # Run the Flask application
        exec python iceberg_browser.py
      '';
      process-compose = {
        depends_on = {
          polaris = {
            condition = "process_healthy";
          };
        };
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 5050;
            path = "/";
          };
          initial_delay_seconds = 3;
          period_seconds = 2;
          failure_threshold = 15;
        };
      };
    };

    # ============================================================================
    # Cost Monitor - AWS Cost Observability
    # ============================================================================
    # Polls AWS Cost Explorer and resource inventory, exposes Prometheus metrics.
    # Metrics endpoint: http://localhost:9876/metrics
    cost-monitor = {
      exec = ''
        echo "Starting AWS cost monitor..."
        echo "Metrics endpoint: http://localhost:9876/metrics"
        echo "Poll interval: 300 seconds (5 minutes)"

        # Use real AWS credentials (not MinIO)
        # AWS_PROFILE reads from ~/.aws/credentials
        export AWS_PROFILE="''${AWS_PROFILE:-default}"
        export AWS_REGION="''${AWS_REGION:-us-east-1}"

        # Run the cost monitor (Flask + Prometheus metrics)
        exec uv run python -m cybersec.cost.monitor
      '';
      process-compose = {
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 9876;
            path = "/health";
          };
          initial_delay_seconds = 10;
          period_seconds = 30;
          failure_threshold = 3;
        };
        availability = {
          restart = "on_failure";
          max_restarts = 5;
        };
      };
    };

    cloudtrail-datagen = {
      exec = ''
        echo "Starting CloudTrail DataGen job..."

        # Set Flink paths
        export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
        FLINK_BIN="$FLINK_HOME/bin/flink"

        # Use uv venv Python which has PyFlink installed
        if [ -f "$PWD/.devenv/state/venv/bin/python3" ]; then
          PYCLIENT="$PWD/.devenv/state/venv/bin/python3"
        else
          PYCLIENT="python3"
        fi

        # Add comprehensive Java module opens for checkpoint serialization
        export FLINK_ENV_JAVA_OPTS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED --add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED --add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/sun.security.action=ALL-UNNAMED"

        # Function to check if job is already running
        check_job_running() {
          curl -s http://localhost:8081/jobs/overview 2>/dev/null | \
            grep -q '"state":"RUNNING"'
        }

        # Check if CloudTrail DataGen is already running
        if check_job_running; then
          echo "CloudTrail DataGen job is already running. Monitoring..."
          # Keep process alive and monitor job status
          while true; do
            if ! check_job_running; then
              echo "Job stopped. Resubmitting..."
              "$FLINK_BIN" run -pyclientexec "$PYCLIENT" -py flink_jobs/cloudtrail_datagen.py
            fi
            sleep 30
          done
        else
          echo "Submitting CloudTrail DataGen job to Flink cluster using flink run..."
          "$FLINK_BIN" run -pyclientexec "$PYCLIENT" -py flink_jobs/cloudtrail_datagen.py

          # Monitor the job and keep process alive
          while true; do
            if ! check_job_running; then
              echo "Job stopped. Resubmitting..."
              "$FLINK_BIN" run -pyclientexec "$PYCLIENT" -py flink_jobs/cloudtrail_datagen.py
            fi
            sleep 30
          done
        fi
      '';
      process-compose = {
        disabled = false;  # Python datagen (10 rows/sec) - runs alongside Java datagen
        availability = {
          restart = "on_failure";
          max_restarts = 3;
        };
        depends_on = {
          flink-taskmanager = {
            condition = "process_healthy";
          };
          polaris-init = {
            condition = "process_completed_successfully";
          };
        };
      };
    };

    # Java CloudTrail DataGen - Pure Java pipeline for benchmarking
    # Generates synthetic CloudTrail events and writes directly to Iceberg
    java-cloudtrail-datagen = {
      exec = ''
        echo "Starting Java CloudTrail DataGen job..."

        # Set Flink paths
        export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
        FLINK_BIN="$FLINK_HOME/bin/flink"
        JAR="$PWD/flink-cyber/flink-common/target/flink-common-2.4.0-iceberg.jar"

        # Configurable rows per second (default 100 for benchmarking)
        RPS="''${JAVA_DATAGEN_RPS:-100}"

        # Add comprehensive Java module opens for checkpoint serialization
        export FLINK_ENV_JAVA_OPTS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED --add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED --add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/sun.security.action=ALL-UNNAMED"

        # Function to check if job is already running
        check_job_running() {
          curl -s http://localhost:8081/jobs/overview 2>/dev/null | \
            grep -q '"state":"RUNNING"'
        }

        # Function to submit the Java datagen job
        submit_job() {
          echo "Submitting Java CloudTrail DataGen job ($RPS rows/sec)..."
          "$FLINK_BIN" run -d \
            -c com.cloudera.cyber.flink.iceberg.CloudTrailDataGenIcebergJob \
            "$JAR" \
            --catalog.uri http://localhost:8181 \
            --warehouse.name cybersec \
            --s3.endpoint http://localhost:9010 \
            --s3.access-key minioadmin \
            --s3.secret-key minioadmin \
            --rows-per-second "$RPS"
        }

        # Check if job is already running, otherwise submit
        if check_job_running; then
          echo "Java CloudTrail DataGen job is already running. Monitoring..."
        else
          submit_job
        fi

        # Monitor the job and keep process alive
        while true; do
          if ! check_job_running; then
            echo "Job stopped. Resubmitting..."
            submit_job
          fi
          sleep 30
        done
      '';
      process-compose = {
        availability = {
          restart = "on_failure";
          max_restarts = 3;
        };
        depends_on = {
          flink-taskmanager = {
            condition = "process_healthy";
          };
          polaris-init = {
            condition = "process_completed_successfully";
          };
          java-datagen-bootstrap = {
            condition = "process_completed_successfully";
          };
        };
      };
    };

    # Bootstrap Polaris realm and principal before server starts
    polaris-bootstrap = {
      exec = ''
        POLARIS_HOME="$PWD/thirdparty/polaris/polaris-bin-1.3.0-incubating"

        # Ensure Polaris bin wrapper scripts exist (creates bin/admin and bin/server)
        if [ ! -x "$POLARIS_HOME/bin/admin" ] || [ ! -x "$POLARIS_HOME/bin/server" ]; then
          echo "🔧 Creating Polaris bin wrapper scripts..."
          "$PWD/scripts/setup_polaris_bin.sh" "$POLARIS_HOME"
        fi

        cd "$POLARIS_HOME"

        # Wait for PostgreSQL to be ready AND polaris_schema to exist
        # The schema is created by devenv's initialScript, which may run after postgres is "healthy"
        echo "⏳ Waiting for PostgreSQL and polaris_schema to be ready..."
        SCHEMA_READY=false
        for i in {1..300}; do
          # Check both: can connect AND schema exists
          SCHEMA_EXISTS=$(psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" -t -c \
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = 'polaris_schema';" 2>/dev/null | tr -d ' ')

          if [ "$SCHEMA_EXISTS" = "1" ]; then
            echo "PostgreSQL is ready with polaris_schema"
            SCHEMA_READY=true
            break
          fi

          if [ $((i % 30)) -eq 0 ]; then
            echo "   Waiting for polaris_schema... ''${i}s elapsed"
          fi
          sleep 1
        done

        if [ "$SCHEMA_READY" != "true" ]; then
          echo "polaris_schema not found after 300 seconds"
          echo "Check that PostgreSQL initialScript ran successfully"
          exit 1
        fi
        
        # Configure database connection for bootstrap
        export QUARKUS_DATASOURCE_DB_KIND=postgresql
        export QUARKUS_DATASOURCE_JDBC_URL="jdbc:postgresql://localhost:5438/iceberg?currentSchema=polaris_schema"
        export QUARKUS_DATASOURCE_USERNAME=cybersec
        export QUARKUS_DATASOURCE_PASSWORD=cybersec
        export POLARIS_PERSISTENCE_TYPE=relational-jdbc
        
        # Check if realm is already bootstrapped by checking if tables exist
        TABLE_COUNT=$(psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'polaris_schema' AND table_name IN ('entities', 'grant_records', 'principal_authentication_data');" 2>/dev/null | tr -d ' ')
        
        if [ "$TABLE_COUNT" = "3" ]; then
          # Tables exist, check if bootstrap principal exists
          PRINCIPAL_COUNT=$(psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" -t -c "SELECT COUNT(*) FROM polaris_schema.principal_authentication_data WHERE realm_id='POLARIS' OR principal_client_id='admin';" 2>/dev/null | tr -d ' ')
          
          if [ "$PRINCIPAL_COUNT" != "0" ]; then
            echo "Polaris realm already bootstrapped (found $PRINCIPAL_COUNT principal(s))"
            exit 0
          else
            echo "Tables exist but no principals found - running bootstrap"
          fi
        else
          echo "🔧 Polaris schema not initialized - will bootstrap fresh"
        fi
        
        # Run bootstrap command with schema version (creates tables and principals)
        echo "🔧 Bootstrapping Polaris with schema v3..."
        if ./bin/admin bootstrap -v 3 -r POLARIS -c POLARIS,admin,admin -p; then
          echo "Polaris realm bootstrapped successfully"
        else
          echo "Failed to bootstrap Polaris realm"
          exit 1
        fi
        
        # Bootstrap process completes and exits
        echo "Bootstrap complete"
        exit 0
      '';
      process-compose = {
        availability = {
          restart = "no";
        };
        depends_on = {
          postgres = {
            condition = "process_healthy";
          };
        };
      };
    };
    
    polaris = {
      exec = ''
        cd thirdparty/polaris/polaris-bin-1.3.0-incubating
        echo "Starting Apache Polaris REST catalog with PostgreSQL persistence..."
        echo "REST API will be available at http://localhost:8181"
        echo "Admin API will be available at http://localhost:8182"
        
        # Configure AWS SDK for MinIO access
        export AWS_ENDPOINT_URL=http://localhost:9010
        export AWS_REGION=us-east-1
        export AWS_ACCESS_KEY_ID=minioadmin
        export AWS_SECRET_ACCESS_KEY=minioadmin
        
        # Quarkus environment variables for PostgreSQL persistence
        export QUARKUS_DATASOURCE_DB_KIND=postgresql
        export QUARKUS_DATASOURCE_JDBC_URL="jdbc:postgresql://localhost:5438/iceberg?currentSchema=polaris_schema"
        export QUARKUS_DATASOURCE_USERNAME=cybersec
        export QUARKUS_DATASOURCE_PASSWORD=cybersec
        export POLARIS_PERSISTENCE_TYPE=relational-jdbc
        
        # AWS SDK v2 properties for S3 endpoint override and Polaris configuration
        # Bind to 0.0.0.0 for WARP/Cloudflare tunnel access
        export JAVA_TOOL_OPTIONS="-Daws.endpointUrl=http://localhost:9010 -Daws.region=us-east-1 -Dquarkus.http.host=0.0.0.0 -Dquarkus.config.locations=$PWD/conf/application.properties"
        
        # Run Polaris server
        exec ./bin/server
      '';
      process-compose = {
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 8182;
            path = "/q/health/ready";
          };
          initial_delay_seconds = 15;
          period_seconds = 3;
          failure_threshold = 30;
        };
        depends_on = {
          polaris-bootstrap = {
            condition = "process_completed_successfully";
          };
          postgres = {
            condition = "process_healthy";
          };
        };
      };
    };

    # Automatic Polaris catalog initialization
    # Runs after Polaris starts, creates catalog with permissions
    # Uses retry logic and verification for robustness
    polaris-init = {
      exec = ''
        source scripts/polaris_bootstrap_helper.sh
        
        # Wait for Polaris to be ready
        if ! wait_for_polaris 60 2; then
          log_error "Polaris did not become ready"
          exit 1
        fi
        
        # Verify bootstrap principal exists
        if ! verify_bootstrap; then
          log_error "Bootstrap principal not found - Polaris may not have bootstrapped correctly"
          exit 1
        fi
        
        # Check if catalog already exists
        if verify_catalog "cybersec"; then
          log_success "Catalog 'cybersec' already exists - skipping initialization"
          exit 0
        fi
        
        # Trigger catalog initialization with retry logic
        if trigger_catalog_init 3 ./setup_polaris_catalog.sh; then
          log_success "Catalog initialization completed and verified"
          exit 0
        else
          echo "Failed to initialize Polaris catalog"
          cat /tmp/polaris-init.log
          exit 1
        fi
        
        # Keep process alive briefly then exit (one-shot initialization)
        sleep 2
        echo "Polaris initialization complete - exiting"
      '';
      process-compose = {
        depends_on = {
          polaris = {
            condition = "process_healthy";
          };
        };
        # Retry on failure with exponential backoff
        availability = {
          restart = "on_failure";
          max_restarts = 3;
          backoff_seconds = 5;
        };
      };
    };

    # ============================================================================
    # Apache NiFi - Data Flow Visualization
    # ============================================================================
    #
    # Visualizes data pipelines and receives OTEL traces from the collector.
    # ListenOTLP processor receives traces on port 4319.
    #
    # Access: http://localhost:8450/nifi
    # Note: First startup may take 1-2 minutes to initialize.

    nifi = {
      exec = ''
        if [ "''${DISABLE_NIFI:-false}" == "true" ]; then
          echo "NiFi disabled (DISABLE_NIFI=true)"
          sleep infinity
        fi

        echo "======================================================"
        echo "  APACHE NIFI - Data Flow Visualization"
        echo "======================================================"
        echo ""

        # Use thirdparty binary distribution (NiFi 2.0.0)
        NIFI_PACKAGE="$PWD/thirdparty/nifi/nifi-2.0.0"

        if [ ! -d "$NIFI_PACKAGE" ]; then
          echo "NiFi not found at $NIFI_PACKAGE"
          echo "Downloading NiFi 2.0.0..."
          echo ""
          if [ -x "$PWD/scripts/setup_nifi_bin.sh" ]; then
            "$PWD/scripts/setup_nifi_bin.sh" 2.0.0
            if [ ! -d "$NIFI_PACKAGE" ]; then
              echo "ERROR: NiFi download failed"
              exit 1
            fi
          else
            echo "ERROR: Setup script not found at $PWD/scripts/setup_nifi_bin.sh"
            exit 1
          fi
        fi

        # NiFi 2.0 requires running from the package directory for proper JAR loading.
        # NiFi reads config from $NIFI_HOME/conf/nifi.properties, so we modify the
        # package config directly for HTTP-only dev mode.
        NIFI_STATE="$DEVENV_STATE/nifi"

        # Create state directories for NiFi data
        mkdir -p "$NIFI_STATE"/{logs,run,database_repository,flowfile_repository,content_repository,provenance_repository,state,work,extensions}
        mkdir -p "$NIFI_STATE/work/nar"
        mkdir -p "$NIFI_STATE/state/local"
        mkdir -p "$NIFI_STATE/status_repository"
        mkdir -p "$NIFI_STATE/flow_archive"

        # Initialize NiFi configuration on first run
        # We modify the package's nifi.properties directly since NiFi reads from $NIFI_HOME/conf
        NIFI_CONFIGURED_MARKER="$NIFI_STATE/.nifi-configured"
        PROPS="$NIFI_PACKAGE/conf/nifi.properties"
        STATE_XML="$NIFI_PACKAGE/conf/state-management.xml"

        if [ ! -f "$NIFI_CONFIGURED_MARKER" ]; then
          echo "Configuring NiFi for HTTP-only development mode..."

          # Backup original config
          cp "$PROPS" "$PROPS.original" 2>/dev/null || true
          cp "$STATE_XML" "$STATE_XML.original" 2>/dev/null || true

          # Web server - bind to all interfaces on port 8450 (HTTP only)
          # Portable sed in-place: use temp file approach (works on both macOS and Linux)
          sed_inplace() {
            local file="''$1"
            local expr="''$2"
            local tmp="''${file}.tmp.''$$"
            sed "''$expr" "''$file" > "''$tmp" && mv "''$tmp" "''$file"
          }

          sed_inplace "$PROPS" 's|^nifi.web.http.host=.*|nifi.web.http.host=0.0.0.0|'
          sed_inplace "$PROPS" 's|^nifi.web.http.port=.*|nifi.web.http.port=8450|'
          # Clear HTTPS - NiFi requires HTTP OR HTTPS, not both
          sed_inplace "$PROPS" 's|^nifi.web.https.host=.*|nifi.web.https.host=|'
          sed_inplace "$PROPS" 's|^nifi.web.https.port=.*|nifi.web.https.port=|'

          # Clear TLS/security properties for HTTP-only mode
          sed_inplace "$PROPS" 's|^nifi.security.keystore=.*|nifi.security.keystore=|'
          sed_inplace "$PROPS" 's|^nifi.security.keystoreType=.*|nifi.security.keystoreType=|'
          sed_inplace "$PROPS" 's|^nifi.security.keystorePasswd=.*|nifi.security.keystorePasswd=|'
          sed_inplace "$PROPS" 's|^nifi.security.keyPasswd=.*|nifi.security.keyPasswd=|'
          sed_inplace "$PROPS" 's|^nifi.security.truststore=.*|nifi.security.truststore=|'
          sed_inplace "$PROPS" 's|^nifi.security.truststoreType=.*|nifi.security.truststoreType=|'
          sed_inplace "$PROPS" 's|^nifi.security.truststorePasswd=.*|nifi.security.truststorePasswd=|'

          # Disable remote input secure mode
          sed_inplace "$PROPS" 's|^nifi.remote.input.secure=.*|nifi.remote.input.secure=false|'

          # Set sensitive properties key (required)
          sed_inplace "$PROPS" 's|^nifi.sensitive.props.key=.*|nifi.sensitive.props.key=cybersec-dev-key-12345|'

          # Configure paths to use state directory for data persistence
          sed_inplace "$PROPS" "s|^\(nifi.flow.configuration.file=\).*|\1$NIFI_STATE/flow.json.gz|"
          sed_inplace "$PROPS" "s|^\(nifi.flow.configuration.json.file=\).*|\1$NIFI_STATE/flow.json.gz|"
          sed_inplace "$PROPS" "s|^\(nifi.flow.configuration.archive.dir=\).*|\1$NIFI_STATE/flow_archive/|"
          sed_inplace "$PROPS" "s|^\(nifi.database.directory=\).*|\1$NIFI_STATE/database_repository|"
          sed_inplace "$PROPS" "s|^\(nifi.flowfile.repository.directory=\).*|\1$NIFI_STATE/flowfile_repository|"
          sed_inplace "$PROPS" "s|^\(nifi.content.repository.directory.default=\).*|\1$NIFI_STATE/content_repository|"
          sed_inplace "$PROPS" "s|^\(nifi.provenance.repository.directory.default=\).*|\1$NIFI_STATE/provenance_repository|"
          sed_inplace "$PROPS" "s|^\(nifi.state.management.configuration.file=\).*|\1$STATE_XML|"
          sed_inplace "$PROPS" "s|^\(nifi.nar.library.autoload.directory=\).*|\1$NIFI_STATE/extensions|"
          sed_inplace "$PROPS" "s|^\(nifi.nar.working.directory=\).*|\1$NIFI_STATE/work/nar/|"
          sed_inplace "$PROPS" "s|^\(nifi.documentation.working.directory=\).*|\1$NIFI_STATE/work/docs/components|"
          sed_inplace "$PROPS" "s|^\(nifi.status.repository.questdb.persist.location=\).*|\1$NIFI_STATE/status_repository|"

          # Update state-management.xml with absolute path for local state
          sed_inplace "$STATE_XML" "s|<property name=\"Directory\">./state/local</property>|<property name=\"Directory\">$NIFI_STATE/state/local</property>|g"

          touch "$NIFI_CONFIGURED_MARKER"
          echo "NiFi configured for development mode."
        fi

        echo ""
        echo "Starting NiFi on port 8450..."
        echo "  Web UI:      http://localhost:8450/nifi"
        echo "  OTLP Port:   4319 (for ListenOTLP processor)"
        echo "  Package:     $NIFI_PACKAGE"
        echo "  Data:        $NIFI_STATE"
        echo ""
        echo "Note: First startup may take 1-2 minutes to initialize."
        echo "      Check logs/nifi-user.log for single-user credentials on first run."
        echo ""

        # Set environment - NiFi requires NIFI_OVERRIDE_NIFIENV=true to respect env vars
        export NIFI_OVERRIDE_NIFIENV="true"
        export NIFI_HOME="$NIFI_PACKAGE"
        export NIFI_LOG_DIR="$NIFI_STATE/logs"
        export NIFI_PID_DIR="$NIFI_STATE/run"

        # MinIO/S3 credentials (for NiFi S3 processors)
        export AWS_ACCESS_KEY_ID="minioadmin"
        export AWS_SECRET_ACCESS_KEY="minioadmin"
        export AWS_ENDPOINT_URL="http://localhost:9010"

        cd "$NIFI_PACKAGE"

        # Run NiFi in foreground mode
        exec "$NIFI_PACKAGE/bin/nifi.sh" run
      '';
      process-compose = {
        depends_on = {
          postgres = {
            condition = "process_healthy";
          };
        };
        readiness_probe = {
          http_get = {
            host = "localhost";
            port = 8450;
            path = "/nifi-api/system-diagnostics";
          };
          initial_delay_seconds = 60;
          period_seconds = 5;
          failure_threshold = 24;
        };
        # Auto-downloads if missing. Disable with DISABLE_NIFI=true (not recommended)
      };
    };
  };

  # Initialize Iceberg catalog in PostgreSQL
  scripts.init-iceberg.exec = ''
    echo "Initializing Iceberg catalog in PostgreSQL..."
    PGPASSWORD=cybersec psql -h localhost -p 5438 -U cybersec -d iceberg -c "
      CREATE SCHEMA IF NOT EXISTS iceberg;
      CREATE TABLE IF NOT EXISTS iceberg.catalog_tables (
        catalog_name VARCHAR(255) NOT NULL,
        table_namespace VARCHAR(255) NOT NULL,
        table_name VARCHAR(255) NOT NULL,
        metadata_location VARCHAR(1024),
        previous_metadata_location VARCHAR(1024),
        PRIMARY KEY (catalog_name, table_namespace, table_name)
      );
    " || echo "Iceberg catalog schema already exists"
  '';
   
  # See full reference at https://devenv.sh/reference/options/
}

