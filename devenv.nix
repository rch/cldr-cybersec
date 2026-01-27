{ pkgs, lib, config, inputs, ... }:

{
  # MinIO data directory - uses DEVENV_STATE by default
  # Override in .cybersec/config.toml or set MINIO_DATA_DIR env var
  # env.MINIO_DATA_DIR = lib.mkForce "/opt/minio/cybersec";  # Example override

  # MinIO/S3 credentials for Metaflow (must override ~/.aws/credentials)
  env.AWS_ACCESS_KEY_ID = "minioadmin";
  env.AWS_SECRET_ACCESS_KEY = "minioadmin";

  # Flink home - built from source in thirdparty/flink
  env.FLINK_HOME = "${config.devenv.root}/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1";


  # https://devenv.sh/packages/
  packages = with pkgs; [
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
    mdbook
    mdbook-d2
    mdbook-katex
    mdbook-mermaid
    opentofu
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
    #uv.sync.enable = true;
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

  # Ensure node_modules exists before devenv's npm integration tries to write checksum
  enterShell = ''
    mkdir -p local-ui/node_modules
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

      # Run conftest
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


  # Flink local cluster for development
  # Web UI: http://localhost:8081
  processes = {
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

    flink-jobmanager = {
      exec = ''
        # Use custom-built Apache Flink 1.20.1 (for Iceberg compatibility)
        export FLINK_HOME="$PWD/thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1"
        export FLINK_STATE_DIR="$DEVENV_STATE/flink"
        export HADOOP_CONF_DIR="$FLINK_HOME/conf"
        mkdir -p "$FLINK_STATE_DIR"/{logs,checkpoints,savepoints}
        
        # Add comprehensive Java module opens for checkpoint serialization
        export FLINK_ENV_JAVA_OPTS="--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED --add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED --add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/sun.security.action=ALL-UNNAMED"
        
        # Run JobManager in foreground mode
        exec "$FLINK_HOME/bin/jobmanager.sh" start-foreground \
          -D jobmanager.rpc.address=localhost \
          -D rest.bind-address=0.0.0.0 \
          -D rest.port=8081 \
          -D state.checkpoints.dir=file://$FLINK_STATE_DIR/checkpoints \
          -D state.savepoints.dir=file://$FLINK_STATE_DIR/savepoints
      '';
      process-compose = {
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
        exec "$FLINK_HOME/bin/taskmanager.sh" start-foreground \
          -D jobmanager.rpc.address=localhost \
          -D taskmanager.numberOfTaskSlots=4 \
          -D taskmanager.tmp.dirs=$FLINK_STATE_DIR/tmp
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

