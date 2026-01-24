"""
Flink DataGen Job for AWS CloudTrail Events
Generates synthetic CloudTrail events for testing the cybersec pipeline

Requirements: apache-flink>=2.2.0
"""

from pyflink.datastream import StreamExecutionEnvironment
from pyflink.table import StreamTableEnvironment, DataTypes, EnvironmentSettings
from pyflink.table.expressions import col
from pyflink.table.udf import udf
import json
import random
from datetime import datetime, timezone


class CloudTrailDataGen:
    """Generate realistic AWS CloudTrail events"""
    
    EVENT_NAMES = [
        "ConsoleLogin", "AssumeRole", "GetObject", "PutObject", "DeleteObject",
        "CreateBucket", "DeleteBucket", "RunInstances", "TerminateInstances",
        "CreateUser", "DeleteUser", "AttachUserPolicy", "DetachUserPolicy",
        "CreateAccessKey", "DeleteAccessKey", "PutBucketPolicy"
    ]
    
    EVENT_SOURCES = [
        "signin.amazonaws.com", "s3.amazonaws.com", "ec2.amazonaws.com",
        "iam.amazonaws.com", "sts.amazonaws.com"
    ]
    
    AWS_REGIONS = [
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"
    ]
    
    USER_AGENTS = [
        "aws-cli/2.13.0", "Boto3/1.28.0", "aws-sdk-java/1.12.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    ]
    
    @staticmethod
    def generate_event() -> str:
        """Generate a single CloudTrail event as JSON string"""
        event = {
            "eventVersion": "1.08",
            "userIdentity": {
                "type": random.choice(["IAMUser", "AssumedRole", "Root"]),
                "principalId": f"AIDA{random.randint(100000000000, 999999999999)}",
                "arn": f"arn:aws:iam::{random.randint(100000000000, 999999999999)}:user/testuser{random.randint(1, 100)}",
                "accountId": str(random.randint(100000000000, 999999999999)),
                "accessKeyId": f"AKIA{random.randint(1000000000000000, 9999999999999999)}"
            },
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "eventSource": random.choice(CloudTrailDataGen.EVENT_SOURCES),
            "eventName": random.choice(CloudTrailDataGen.EVENT_NAMES),
            "awsRegion": random.choice(CloudTrailDataGen.AWS_REGIONS),
            "sourceIPAddress": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
            "userAgent": random.choice(CloudTrailDataGen.USER_AGENTS),
            "requestParameters": {
                "bucketName": f"test-bucket-{random.randint(1, 1000)}",
                "key": f"data/file-{random.randint(1, 10000)}.json"
            },
            "responseElements": {
                "requestId": f"{random.randint(10**15, 10**16-1):016x}"
            },
            "requestID": f"{random.randint(10**15, 10**16-1):016x}",
            "eventID": f"{random.randint(10**31, 10**32-1):032x}",
            "readOnly": random.choice([True, False]),
            "eventType": "AwsApiCall",
            "managementEvent": True,
            "recipientAccountId": str(random.randint(100000000000, 999999999999)),
            "eventCategory": "Management",
            "metric_history": [random.randint(10, 100) for _ in range(15)] if random.random() > 0.7 else None
        }
        return json.dumps(event)


def create_cloudtrail_datagen_job():
    """Create and run CloudTrail data generation job"""
    import os
    
    # Set Flink configuration to use JARs from the Flink distribution
    flink_home = os.path.join(os.getcwd(), "thirdparty/flink/flink-dist/target/flink-1.20.1-bin/flink-1.20.1")
    os.environ.setdefault('FLINK_HOME', flink_home)
    
    # Create streaming environment
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    
    # Enable checkpointing for data commits
    # Checkpoints trigger Iceberg commits - without this, data stays buffered!
    env.enable_checkpointing(10000)  # Checkpoint every 10 seconds
    
    # Create table environment with streaming settings
    settings = EnvironmentSettings.in_streaming_mode()
    t_env = StreamTableEnvironment.create(env, settings)
    
    # Set table configuration for faster commits
    t_env.get_config().set("table.exec.sink.not-null-enforcer", "drop")
    t_env.get_config().set("execution.checkpointing.interval", "10s")
    
    # Set pipeline JAR configuration - find Iceberg runtime JAR (version may vary)
    import glob
    lib_dir = os.path.join(flink_home, "lib")
    iceberg_jars = glob.glob(os.path.join(lib_dir, "iceberg-flink-runtime-1.20-*.jar"))
    if iceberg_jars:
        t_env.get_config().set("pipeline.jars", f"file://{iceberg_jars[0]}")
    
    # Register UDF for generating CloudTrail events
    t_env.create_temporary_system_function(
        "generate_cloudtrail", 
        udf(lambda: CloudTrailDataGen.generate_event(), result_type=DataTypes.STRING())
    )
    
    # Create Iceberg catalog for Polaris REST
    # In Polaris REST API, the catalog is accessed at /api/catalog/warehouse_name
    # The catalog name in Flink must match the warehouse name in Polaris
    # Uses S3FileIO (via iceberg-aws-bundle) to avoid Hadoop dependencies
    t_env.execute_sql("""
        CREATE CATALOG cybersec WITH (
            'type' = 'iceberg',
            'catalog-type' = 'rest',
            'uri' = 'http://localhost:8181/api/catalog',
            'credential' = 'admin:admin',
            'scope' = 'PRINCIPAL_ROLE:ALL',
            'warehouse' = 'cybersec',
            'io-impl' = 'org.apache.iceberg.aws.s3.S3FileIO',
            's3.endpoint' = 'http://localhost:9010',
            's3.region' = 'us-east-1',
            's3.path-style-access' = 'true',
            's3.access-key-id' = 'minioadmin',
            's3.secret-access-key' = 'minioadmin'
        )
    """)
    
    # Use the catalog
    t_env.use_catalog('cybersec')
    
    # Create database if not exists (default is a reserved keyword, must be quoted)
    t_env.execute_sql("CREATE DATABASE IF NOT EXISTS `default`")
    t_env.use_database('default')
    
    # Create Iceberg sink table for CloudTrail events
    # Disable write.metadata.metrics.default to avoid classloader conflicts
    t_env.execute_sql("""
        CREATE TABLE IF NOT EXISTS cloudtrail_events (
            event_data STRING,
            event_time TIMESTAMP(3)
        ) WITH (
            'write.metadata.metrics.default' = 'none'
        )
    """)
    
    # Create a temporary datagen source table (not persisted to Iceberg catalog)
    # Temporary tables support computed columns and connectors like datagen
    t_env.execute_sql("""
        CREATE TEMPORARY TABLE datagen_source (
            event_id BIGINT,
            event_timestamp AS PROCTIME()
        ) WITH (
            'connector' = 'datagen',
            'rows-per-second' = '10',
            'fields.event_id.kind' = 'sequence',
            'fields.event_id.start' = '1',
            'fields.event_id.end' = '1000000'
        )
    """)
    
    # Generate and insert CloudTrail events
    # .wait() blocks until the job completes (for bounded) or is cancelled (for streaming)
    result = t_env.execute_sql("""
        INSERT INTO cloudtrail_events
        SELECT
            generate_cloudtrail() as event_data,
            CURRENT_TIMESTAMP as event_time
        FROM datagen_source
    """)
    print("Job submitted, waiting for completion...")
    result.wait()


if __name__ == "__main__":
    print("Starting CloudTrail DataGen Job...")
    create_cloudtrail_datagen_job()
