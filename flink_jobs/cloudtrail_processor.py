"""
Flink Job to Process CloudTrail Events
Parses CloudTrail events and enriches them with threat intelligence

Requirements: apache-flink>=2.2.0
"""

from pyflink.table import StreamTableEnvironment, EnvironmentSettings, DataTypes
from pyflink.table.udf import udf
import json
from datetime import datetime


def create_cloudtrail_processor_job():
    """
    Process CloudTrail events: parse, enrich, and prepare for Iceberg storage
    """
    env = StreamExecutionEnvironment.get_execution_environment()
    # Create streaming environment with checkpointing
    env_settings = EnvironmentSettings.in_streaming_mode()
    t_env = StreamTableEnvironment.create(environment_settings=env_settings)
    
    # Configure checkpointing and parallelism
    t_env.get_config().set("execution.checkpointing.interval", "60s")
    t_env.get_config().set("parallelism.default", "2"
    # Define UDF for parsing CloudTrail JSON
    @udf(result_type=DataTypes.ROW([
        DataTypes.FIELD("event_version", DataTypes.STRING()),
        DataTypes.FIELD("event_time", DataTypes.STRING()),
        DataTypes.FIELD("event_source", DataTypes.STRING()),
        DataTypes.FIELD("event_name", DataTypes.STRING()),
        DataTypes.FIELD("aws_region", DataTypes.STRING()),
        DataTypes.FIELD("source_ip", DataTypes.STRING()),
        DataTypes.FIELD("user_agent", DataTypes.STRING()),
        DataTypes.FIELD("user_type", DataTypes.STRING()),
        DataTypes.FIELD("user_arn", DataTypes.STRING()),
        DataTypes.FIELD("account_id", DataTypes.STRING()),
        DataTypes.FIELD("event_id", DataTypes.STRING()),
        DataTypes.FIELD("read_only", DataTypes.BOOLEAN()),
        DataTypes.FIELD("event_type", DataTypes.STRING()),
    ]))
    def parse_cloudtrail(event_json: str):
        try:
            event = json.loads(event_json)
            return (
                event.get("eventVersion", ""),
                event.get("eventTime", ""),
                event.get("eventSource", ""),
                event.get("eventName", ""),
                event.get("awsRegion", ""),
                event.get("sourceIPAddress", ""),
                event.get("userAgent", ""),
                event.get("userIdentity", {}).get("type", ""),
                event.get("userIdentity", {}).get("arn", ""),
                event.get("userIdentity", {}).get("accountId", ""),
                event.get("eventID", ""),
                event.get("readOnly", False),
                event.get("eventType", "")
            )
        except Exception as e:
            print(f"Error parsing CloudTrail event: {e}")
            return ("", "", "", "", "", "", "", "", "", "", "", False, "")
    
    # Register the UDF
    t_env.create_temporary_function("parse_cloudtrail", parse_cloudtrail)
    
    # Create source table reading from Kafka
    t_env.execute_sql("""
        CREATE TABLE cloudtrail_raw (
            event_data STRING,
            event_time TIMESTAMP(3),
            WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
        ) WITH (
            'connector' = 'kafka',
            'topic' = 'cloudtrail-raw',
            'properties.bootstrap.servers' = 'localhost:9092',
            'properties.group.id' = 'cloudtrail-processor',
            'scan.startup.mode' = 'latest-offset',
            'format' = 'json'
        )
    """)
    
    # Create intermediate table for parsed events
    t_env.execute_sql("""
        CREATE TABLE cloudtrail_parsed (
            event_version STRING,
            event_timestamp STRING,
            event_source STRING,
            event_name STRING,
            aws_region STRING,
            source_ip STRING,
            user_agent STRING,
            user_type STRING,
            user_arn STRING,
            account_id STRING,
            event_id STRING,
            read_only BOOLEAN,
            event_type STRING,
            processing_time TIMESTAMP(3)
        ) WITH (
            'connector' = 'kafka',
            'topic' = 'cloudtrail-parsed',
            'properties.bootstrap.servers' = 'localhost:9092',
            'format' = 'json'
        )
    """)
    
    # Parse and forward CloudTrail events
    t_env.execute_sql("""
        INSERT INTO cloudtrail_parsed
        SELECT 
            parsed.event_version,
            parsed.event_time,
            parsed.event_source,
            parsed.event_name,
            parsed.aws_region,
            parsed.source_ip,
            parsed.user_agent,
            parsed.user_type,
            parsed.user_arn,
            parsed.account_id,
            parsed.event_id,
            parsed.read_only,
            parsed.event_type,
            CURRENT_TIMESTAMP as processing_time
        FROM (
            SELECT parse_cloudtrail(event_data) as parsed
            FROM cloudtrail_raw
        )
    """)


if __name__ == "__main__":
    print("Starting CloudTrail Processor Job...")
    create_cloudtrail_processor_job()
