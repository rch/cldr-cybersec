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
    ];
    port = 5438;
    listen_addresses = "*";  # Enable TCP from K8s pods and local clients
    settings = {
      shared_preload_libraries = "pg_cron,age";
      "cron.database_name" = "cybersec";
    };
    initialScript = ''
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
        # Set up writable Flink state directory
        export FLINK_HOME="${pkgs.flink}/opt/flink"
        export FLINK_STATE_DIR="$DEVENV_STATE/flink"
        mkdir -p "$FLINK_STATE_DIR"/{logs,checkpoints,savepoints}
        
        # Run JobManager in foreground mode
        exec ${pkgs.flink}/opt/flink/bin/jobmanager.sh start-foreground \
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
        # Set up writable Flink state directory  
        export FLINK_HOME="${pkgs.flink}/opt/flink"
        export FLINK_STATE_DIR="$DEVENV_STATE/flink"
        mkdir -p "$FLINK_STATE_DIR"/{logs,tmp}
        
        # Run TaskManager in foreground mode
        exec ${pkgs.flink}/opt/flink/bin/taskmanager.sh start-foreground \
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
  };

  # Initialize Iceberg catalog in PostgreSQL
  scripts.init-iceberg.exec = ''
    echo "Initializing Iceberg catalog in PostgreSQL..."
    psql -h localhost -p 5438 -U postgres -d cybersec -c "
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

