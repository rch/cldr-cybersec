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
            "eventCategory": "Management"
        }
        return json.dumps(event)


def create_cloudtrail_datagen_job():
    """
    # Create streaming environment
    env_settings = EnvironmentSettings.in_streaming_mode()
    t_env = StreamTableEnvironment.create(environment_settings=env_settings)
    
    # Set parallelism
    t_env.get_config().set("parallelism.default", "1"
    
    t_env = StreamTableEnvironment.create(env)
    
    # Register UDF for generating CloudTrail events
    @udf(result_type=DataTypes.STRING())
    def generate_cloudtrail_event():
        return CloudTrailDataGen.generate_event()
    
    t_env.create_temporary_function("generate_cloudtrail", generate_cloudtrail_event)
    
    # Create a datagen source table that generates events
    t_env.execute_sql("""
        CREATE TABLE datagen_source (
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
    
    # Create Kafka sink table for CloudTrail events
    t_env.execute_sql("""
        CREATE TABLE cloudtrail_events (
            event_data STRING,
            event_time TIMESTAMP(3)
        ) WITH (
            'connector' = 'kafka',
            'topic' = 'cloudtrail-raw',
            'properties.bootstrap.servers' = 'localhost:9092',
            'format' = 'json',
            'json.fail-on-missing-field' = 'false',
            'json.ignore-parse-errors' = 'true'
        )
    """)
    
    # Generate and insert CloudTrail events
    t_env.execute_sql("""
        INSERT INTO cloudtrail_events
        SELECT 
            generate_cloudtrail() as event_data,
            CURRENT_TIMESTAMP as event_time
        FROM datagen_source
    """)


if __name__ == "__main__":
    print("Starting CloudTrail DataGen Job...")
    create_cloudtrail_datagen_job()
