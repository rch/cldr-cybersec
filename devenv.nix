{ pkgs, lib, config, inputs, ... }:

{
  # Override MinIO data directory to use RAID storage
  env.MINIO_DATA_DIR = lib.mkForce "/opt/minio/cybersec";

  # MinIO/S3 credentials for Metaflow (must override ~/.aws/credentials)
  env.AWS_ACCESS_KEY_ID = "minioadmin";
  env.AWS_SECRET_ACCESS_KEY = "minioadmin";


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
    listenAddress = "127.0.0.1:9010";
    consoleAddress = "127.0.0.1:9011";
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
  
  languages.typescript = {
    enable=true;
  }; 

  tasks = {
    "docs:build".exec = "mdbook build docs";
    "docs:open".exec = "mdbook build docs --open";
    
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
      
      echo "🔥 Aggressively stopping all processes..."
      
      # Kill process-compose and all related processes
      pkill -9 -f "process-compose" 2>/dev/null || true
      pkill -9 -f "iceberg-browser" 2>/dev/null || true
      pkill -9 -f "cloudtrail" 2>/dev/null || true
      pkill -9 -f "devenv" 2>/dev/null || true
      
      # Kill by port (Polaris, PostgreSQL, MinIO, Flink, Iceberg Browser)
      lsof -ti:8181,8182,5438,9010,9011,8081,5050 2>/dev/null | xargs kill -9 2>/dev/null || true
      
      # Kill remaining service processes
      pgrep -fl "minio|postgres|flink|taskmanager|jobmanager|quarkus|polaris" | awk '{print $1}' | xargs kill -9 2>/dev/null || true
      
      # Verify critical ports are released
      log_info "Verifying ports are released..."
      for port in 8181 8182 5438; do
        wait_for_port_release $port 5 2 || log_warn "Port $port may still be in use"
      done
      
      # Clean up socket and temp files
      rm -f /var/folders/sx/g8_f354d6wncq7l6x9nqq08r0000gn/T/devenv-*/pc.sock 2>/dev/null || true
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

  # Flink local cluster for development
  # Web UI: http://localhost:8081
  processes = {
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
              "$FLINK_BIN" run -pyclientexec python -py flink_jobs/cloudtrail_datagen.py
            fi
            sleep 30
          done
        else
          echo "Submitting CloudTrail DataGen job to Flink cluster using flink run..."
          "$FLINK_BIN" run -pyclientexec python -py flink_jobs/cloudtrail_datagen.py
          
          # Monitor the job and keep process alive
          while true; do
            if ! check_job_running; then
              echo "Job stopped. Resubmitting..."
              "$FLINK_BIN" run -pyclientexec python -py flink_jobs/cloudtrail_datagen.py
            fi
            sleep 30
          done
        fi
      '';
      process-compose = {
        # Disable auto-start - job submission is handled by verify_e2e() in restart:clean
        availability = {
          restart = "no";
        };
        disabled = true;
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
        cd thirdparty/polaris/polaris-bin-1.3.0-incubating
        
        # Wait for PostgreSQL
        echo "⏳ Waiting for PostgreSQL to be ready..."
        for i in {1..30}; do
          if psql "postgresql://cybersec:cybersec@localhost:5438/iceberg" -c "SELECT 1" > /dev/null 2>&1; then
            echo "✅ PostgreSQL is ready"
            break
          fi
          if [ $i -eq 30 ]; then
            echo "❌ PostgreSQL failed to become ready"
            exit 1
          fi
          sleep 1
        done
        
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
        export JAVA_TOOL_OPTIONS="-Daws.endpointUrl=http://localhost:9010 -Daws.region=us-east-1 -Dquarkus.config.locations=$PWD/conf/application.properties"
        
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

