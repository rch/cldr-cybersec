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
      
      -- Create extensions
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

    polaris = {
      exec = ''
        cd thirdparty/polaris/polaris-bin-1.3.0-incubating
        echo "Starting Apache Polaris REST catalog..."
        echo "REST API will be available at http://localhost:8181"
        echo "Admin API will be available at http://localhost:8182"
        
        # Set bootstrap credentials (realm: POLARIS, client_id: admin, secret: admin)
        export POLARIS_JAVA_OPTS="-Dpolaris.bootstrap.credentials=POLARIS,admin,admin"
        
        # Configure AWS SDK for MinIO access
        export AWS_ENDPOINT_URL=http://localhost:9010
        export AWS_REGION=us-east-1
        export AWS_ACCESS_KEY_ID=minioadmin
        export AWS_SECRET_ACCESS_KEY=minioadmin
        
        # AWS SDK v2 properties for S3 endpoint override
        export JAVA_TOOL_OPTIONS="-Daws.endpointUrl=http://localhost:9010 -Daws.region=us-east-1"
        
        # Run Polaris server with config file
        exec ./bin/server ../../polaris-server.yml
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

