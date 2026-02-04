{ pkgs, lib, config, inputs, ... }:

{
  dotenv.enable = true;

  # MinIO data directory - uses DEVENV_STATE by default
  # Override in .cybersec/config.toml or set MINIO_DATA_DIR env var
  # env.MINIO_DATA_DIR = lib.mkForce "/opt/minio/cybersec";  # Example override

  # MinIO/S3 credentials for Metaflow (must override ~/.aws/credentials)
  env.AWS_ACCESS_KEY_ID = "minioadmin";
  env.AWS_SECRET_ACCESS_KEY = "minioadmin";

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

    # Create Polaris bin wrapper scripts if needed
    POLARIS_HOME="$PWD/thirdparty/polaris/polaris-bin-1.3.0-incubating"
    if [ -d "$POLARIS_HOME" ] && [ ! -x "$POLARIS_HOME/bin/admin" ]; then
      echo "Creating Polaris bin wrapper scripts..."
      "$PWD/scripts/setup_polaris_bin.sh" "$POLARIS_HOME" 2>/dev/null || true
    fi

    # First-time setup hint
    if [ ! -f "thirdparty/flink/pom.xml" ]; then
      echo ""
      echo "⚠️  First-time setup detected. Run: devenv tasks run restart:clean"
      echo "   This initializes submodules and builds Flink (~15 min on first run)"
      echo ""
    fi
  '';
  
  languages.typescript = {
    enable=true;
  }; 

  tasks = {
    "docs:build".exec = "mdbook build docs";
    "docs:open".exec = "mdbook build docs --open";

    # Policy validation using conftest
    "policy:check".exec = ''
      echo "🔍 Running policy validation..."

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
        echo "⚠️  Policy violations detected. Fix issues above and re-run."
        exit 1
      }
      echo ""
      echo "✅ All policy checks passed"
    '';

    "policy:generate".exec = ''
      echo "📝 Generating environment config..."
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
      
      echo "🔍 Checking Polaris catalog status..."
      
      # Check if Polaris is running
      if ! wait_for_polaris 1 0; then
        echo "❌ Polaris is not running. Start it with: devenv up"
        exit 1
      fi
      
      # Check if catalog exists
      if verify_catalog "cybersec"; then
        echo "✅ Polaris is properly configured"
      else
        echo "⚠️  Catalog 'cybersec' not found"
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
      cd infra/aws/ansible
      ansible-playbook playbooks/jupyterhub.yml
      echo "✅ JupyterHub deployed"
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

      echo "🔥 Aggressively stopping all processes..."

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
      for port in 8181 8182 5438 9010 9011 8081 5050 8450 4317 4318 8888 8889 9090; do
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

      echo "✅ All processes killed and temp files cleaned"
      
      # Remove PostgreSQL data to trigger fresh initialization
      echo "🗑️  Removing PostgreSQL data for fresh initialization..."
      if [ -d "$DEVENV_STATE/postgres" ]; then
        rm -rf "$DEVENV_STATE/postgres"
        echo "✅ PostgreSQL data removed"
      else
        echo "ℹ️  No PostgreSQL data found to remove"
      fi
      
      echo "ℹ️  Note: Fresh PostgreSQL will initialize with polaris_schema"
      echo "ℹ️  Note: Polaris catalog will be created automatically on startup"
      sleep 2
      
      # Check if bootstrap is needed (missing connectors, etc.)
      echo "🔍 Checking bootstrap status..."
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
        log_error "E2E verification failed - check logs for details"
        exit 1
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
    ];
  };
  # Process-compose managed services (Flink, Polaris, Dask, auxiliary tooling)
  processes = {
    podman-runtime = {
      exec = ''
        set -euo pipefail

        if ! command -v podman >/dev/null 2>&1; then
          echo "❌ podman CLI not found in dev environment"
          exit 1
        fi

        echo "Ensuring Podman environment is available for k3d..."

        if podman machine list >/dev/null 2>&1; then
          MACHINE_JSON=$(podman machine list --format=json 2>/dev/null || echo "[]")
          MACHINE_NAME=$(echo "$MACHINE_JSON" | jq -r 'map(select(.Name != null)) | map(.Name)[0] // empty')
          if [ -n "$MACHINE_NAME" ]; then
            MACHINE_STATE=$(echo "$MACHINE_JSON" | jq -r --arg name "$MACHINE_NAME" '.[] | select(.Name==$name) | .State // ""')
            if [ "$MACHINE_STATE" != "Running" ]; then
              echo "Starting Podman machine '$MACHINE_NAME'..."
              podman machine start "$MACHINE_NAME"
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
        availability = {
          restart = "always";
        };
      };
    };

    k3d-cluster = {
      exec = ''
        set -euo pipefail

        CLUSTER_NAME=''${K3D_CLUSTER_NAME:-cybersec}
        KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"

        export K3D_FIX_DNS=1

        if ! command -v k3d >/dev/null 2>&1; then
          echo "❌ k3d CLI not found"
          exit 1
        fi

        if ! command -v kubectl >/dev/null 2>&1; then
          echo "❌ kubectl CLI not found"
          exit 1
        fi

        if [ -z "''${DOCKER_HOST:-}" ]; then
          if command -v podman >/dev/null 2>&1 && podman machine list >/dev/null 2>&1; then
            MACHINE_JSON=$(podman machine list --format=json 2>/dev/null || echo "[]")
            MACHINE_NAME=$(echo "$MACHINE_JSON" | jq -r 'map(select(.Name != null)) | map(.Name)[0] // empty')
            if [ -n "$MACHINE_NAME" ]; then
              SOCKET_PATH=$(podman machine inspect "$MACHINE_NAME" 2>/dev/null | jq -r '.[0].ConnectionInfo.PodmanSocket.Path // empty')
              if [ -n "$SOCKET_PATH" ] && [ -S "$SOCKET_PATH" ]; then
                export DOCKER_HOST="unix://$SOCKET_PATH"
                export K3D_HIDE_WARNING_ROOTLESS=1
                echo "Using Podman machine socket at $SOCKET_PATH for k3d"
              fi
            fi
          fi
        fi

        mkdir -p "$(dirname "$KUBECONFIG_PATH")"

        if ! k3d cluster list | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER_NAME"; then
          echo "Creating k3d cluster '$CLUSTER_NAME'..."
          k3d cluster create "$CLUSTER_NAME" \
            --servers 1 \
            --agents 1 \
            --k3s-arg '--disable=traefik@server:0' \
            --k3s-arg '--disable=servicelb@server:0' \
            --port '8786:30086@server:0' \
            --port '8787:30087@server:0' \
            --wait --timeout 300s
        else
          echo "k3d cluster '$CLUSTER_NAME' already exists; ensuring it is started..."
          k3d cluster start "$CLUSTER_NAME" || true
        fi

        TMP_KUBECONFIG="$KUBECONFIG_PATH.tmp"
        k3d kubeconfig get "$CLUSTER_NAME" > "$TMP_KUBECONFIG"
        mv "$TMP_KUBECONFIG" "$KUBECONFIG_PATH"
        chmod 600 "$KUBECONFIG_PATH"
        export KUBECONFIG="$KUBECONFIG_PATH"

        echo "Waiting for Kubernetes API..."
        for _ in $(seq 1 60); do
          if kubectl get nodes >/dev/null 2>&1; then
            break
          fi
          sleep 2
        done

        kubectl wait --for=condition=Ready node --all --timeout=300s

        echo "✅ k3d cluster '$CLUSTER_NAME' is ready"

        while true; do
          if ! kubectl get nodes >/dev/null 2>&1; then
            echo "Lost connection to cluster; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
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

        KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        export KUBECONFIG="$KUBECONFIG_PATH"

        if [ ! -f "$KUBECONFIG_PATH" ]; then
          echo "❌ kubeconfig not found at $KUBECONFIG_PATH"
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

        echo "✅ Dask operator installed"

        while true; do
          if ! kubectl get pods -n dask-operator >/dev/null 2>&1; then
            echo "Unable to query Dask operator pods; exiting for restart"
            exit 1
          fi
          sleep 30
        done
      '';
      process-compose = {
        depends_on = {
          k3d-cluster = {
            condition = "process_started";
          };
        };
        readiness_probe = {
          exec = {
            command = "KUBECONFIG=$PWD/.devenv/state/kubeconfig kubectl get deploy -n dask-operator dask-kubernetes-operator >/dev/null";
          };
          initial_delay_seconds = 10;
          period_seconds = 10;
          failure_threshold = 6;
        };
      };
    };

    dask-cluster = {
      exec = ''
        set -euo pipefail

        MANIFEST="$PWD/infra/dask/dask-cluster.yaml"
        if [ ! -f "$MANIFEST" ]; then
          echo "❌ Dask manifest not found at $MANIFEST"
          exit 1
        fi

        KUBECONFIG_PATH="$PWD/.devenv/state/kubeconfig"
        export KUBECONFIG="$KUBECONFIG_PATH"

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
          echo "❌ Dask cluster did not reach Running status (last status: ''${STATUS:-unknown})"
          kubectl get daskclusters -n dask || true
          exit 1
        fi

        echo "✅ Dask cluster is running"
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
        depends_on = {
          dask-operator = {
            condition = "process_healthy";
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
          echo "✅ Flink $FLINK_VERSION already built"
        else
          echo "🔧 Building Flink $FLINK_VERSION from source (this takes 10-15 minutes)..."

          # Ensure submodules are initialized
          if [ ! -f "thirdparty/flink/pom.xml" ]; then
            echo "Initializing git submodules..."
            git submodule update --init --recursive
          fi

          cd thirdparty/flink
          mvn clean install -DskipTests -Dfast -T 1C
          cd ../..

          if [ -x "$FLINK_DIST/bin/flink" ]; then
            echo "✅ Flink built successfully"
          else
            echo "❌ Flink build failed"
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
                echo "  ✅ Installed: $(basename $jar)"
                break
              fi
            done

            # Copy AWS bundle JAR
            for jar in aws-bundle/build/libs/iceberg-aws-bundle-*.jar; do
              if [ -f "$jar" ] && [[ "$jar" != *"-sources.jar" ]] && [[ "$jar" != *"-javadoc.jar" ]]; then
                cp "$jar" "$FLINK_DIST/lib/"
                echo "  ✅ Installed: $(basename $jar)"
                break
              fi
            done

            cd ../..
          else
            echo "⚠️  Iceberg submodule not available - run: git submodule update --init thirdparty/iceberg"
          fi

          # Verify installation
          ICEBERG_JAR=$(ls "$FLINK_DIST/lib/iceberg-flink-runtime-1.20-"*.jar 2>/dev/null | head -1)
          AWS_BUNDLE_JAR=$(ls "$FLINK_DIST/lib/iceberg-aws-bundle-"*.jar 2>/dev/null | head -1)
          if [ -z "$ICEBERG_JAR" ] || [ -z "$AWS_BUNDLE_JAR" ]; then
            echo "❌ Iceberg JAR installation failed"
            echo "   Missing: iceberg-flink-runtime and/or iceberg-aws-bundle"
            echo "   Run: /health fix --apply"
            exit 1
          fi
        else
          echo "✅ Iceberg connectors already installed"
        fi

        # Copy additional required JARs from Flink opt/ directory
        if [ ! -f "$FLINK_DIST/lib/flink-s3-fs-hadoop-1.20.1.jar" ]; then
          if [ -f "$FLINK_DIST/opt/flink-s3-fs-hadoop-1.20.1.jar" ]; then
            cp "$FLINK_DIST/opt/flink-s3-fs-hadoop-1.20.1.jar" "$FLINK_DIST/lib/"
            echo "✅ Copied flink-s3-fs-hadoop to lib (S3 filesystem support)"
          fi
        fi

        if [ ! -f "$FLINK_DIST/lib/flink-python-1.20.1.jar" ]; then
          if [ -f "$FLINK_DIST/opt/flink-python-1.20.1.jar" ]; then
            cp "$FLINK_DIST/opt/flink-python-1.20.1.jar" "$FLINK_DIST/lib/"
            echo "✅ Copied flink-python to lib (PyFlink support)"
          fi
        fi

        echo "✅ Flink bootstrap complete"
        exit 0
      '';
      process-compose = {
        availability = {
          restart = "no";
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
          echo "❌ Flink not found at $FLINK_HOME"
          echo "   Run: devenv tasks run restart:clean"
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
            echo "✅ PostgreSQL is ready with polaris_schema"
            SCHEMA_READY=true
            break
          fi

          if [ $((i % 30)) -eq 0 ]; then
            echo "   Waiting for polaris_schema... ''${i}s elapsed"
          fi
          sleep 1
        done

        if [ "$SCHEMA_READY" != "true" ]; then
          echo "❌ polaris_schema not found after 300 seconds"
          echo "   Check that PostgreSQL initialScript ran successfully"
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
            echo "✅ Polaris realm already bootstrapped (found $PRINCIPAL_COUNT principal(s))"
            exit 0
          else
            echo "⚠️  Tables exist but no principals found - running bootstrap"
          fi
        else
          echo "🔧 Polaris schema not initialized - will bootstrap fresh"
        fi
        
        # Run bootstrap command with schema version (creates tables and principals)
        echo "🔧 Bootstrapping Polaris with schema v3..."
        if ./bin/admin bootstrap -v 3 -r POLARIS -c POLARIS,admin,admin -p; then
          echo "✅ Polaris realm bootstrapped successfully"
        else
          echo "❌ Failed to bootstrap Polaris realm"
          exit 1
        fi
        
        # Bootstrap process completes and exits
        echo "✅ Bootstrap complete"
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
          echo "❌ Failed to initialize Polaris catalog"
          cat /tmp/polaris-init.log
          exit 1
        fi
        
        # Keep process alive briefly then exit (one-shot initialization)
        sleep 2
        echo "✅ Polaris initialization complete - exiting"
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

