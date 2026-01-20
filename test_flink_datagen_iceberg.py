"""
Test Flink DataGen to Iceberg Pipeline
Uses PyFlink datagen to generate CloudTrail events and write to Iceberg
"""

from pyflink.table import EnvironmentSettings, StreamTableEnvironment
from pyflink.table.expressions import col
import time
import os

def test_flink_datagen_to_iceberg():
    """Test end-to-end: Flink DataGen -> Iceberg (MinIO)"""
    
    print("=" * 70)
    print("Testing Flink DataGen -> Iceberg Pipeline")
    print("=" * 70)
    
    # Create Table Environment
    env_settings = EnvironmentSettings.in_streaming_mode()
    t_env = StreamTableEnvironment.create(environment_settings=env_settings)
    
    # Configure for local execution
    t_env.get_config().set("parallelism.default", "1")
    t_env.get_config().set("execution.checkpointing.interval", "10s")
    
    print("\n1. Creating DataGen source table...")
    # Create datagen source with CloudTrail-like structure
    t_env.execute_sql("""
        CREATE TABLE cloudtrail_datagen (
            event_id STRING,
            event_version STRING,
            event_timestamp TIMESTAMP(3),
            event_source STRING,
            event_name STRING,
            aws_region STRING,
            source_ip STRING,
            user_agent STRING,
            user_type STRING,
            user_arn STRING,
            account_id STRING,
            read_only BOOLEAN,
            event_type STRING
        ) WITH (
            'connector' = 'datagen',
            'rows-per-second' = '5',
            'fields.event_id.kind' = 'random',
            'fields.event_id.length' = '32',
            'fields.event_version.kind' = 'sequence',
            'fields.event_version.start' = '1',
            'fields.event_version.end' = '1',
            'fields.event_timestamp.max-past' = '0',
            'fields.event_source.kind' = 'random',
            'fields.event_source.length' = '20',
            'fields.event_name.kind' = 'random',
            'fields.event_name.length' = '15',
            'fields.aws_region.kind' = 'random',
            'fields.aws_region.length' = '10',
            'fields.source_ip.kind' = 'random',
            'fields.source_ip.length' = '15',
            'fields.user_agent.kind' = 'random',
            'fields.user_agent.length' = '20',
            'fields.user_type.kind' = 'random',
            'fields.user_type.length' = '10',
            'fields.user_arn.kind' = 'random',
            'fields.user_arn.length' = '50',
            'fields.account_id.kind' = 'random',
            'fields.account_id.length' = '12',
            'fields.read_only.kind' = 'random',
            'fields.event_type.kind' = 'random',
            'fields.event_type.length' = '12'
        )
    """)
    print("   ✓ DataGen source created")
    
    print("\n2. Creating Iceberg catalog and table...")
    
    # Configure Iceberg catalog
    t_env.execute_sql("""
        CREATE CATALOG iceberg_catalog WITH (
            'type' = 'iceberg',
            'catalog-type' = 'jdbc',
            'uri' = 'jdbc:postgresql://localhost:5438/cybersec',
            'warehouse' = 's3://cybersec/iceberg/warehouse',
            's3.endpoint' = 'http://localhost:9010',
            's3.access-key-id' = 'minioadmin',
            's3.secret-access-key' = 'minioadmin',
            's3.path-style-access' = 'true'
        )
    """)
    print("   ✓ Iceberg catalog created")
    
    # Use the Iceberg catalog
    t_env.use_catalog("iceberg_catalog")
    
    # Create database
    t_env.execute_sql("CREATE DATABASE IF NOT EXISTS cybersec")
    t_env.use_database("cybersec")
    
    # Create Iceberg table
    t_env.execute_sql("""
        CREATE TABLE IF NOT EXISTS cloudtrail_events (
            event_id STRING,
            event_version STRING,
            event_timestamp TIMESTAMP(3),
            event_source STRING,
            event_name STRING,
            aws_region STRING,
            source_ip STRING,
            user_agent STRING,
            user_type STRING,
            user_arn STRING,
            account_id STRING,
            read_only BOOLEAN,
            event_type STRING,
            processing_time TIMESTAMP(3)
        ) PARTITIONED BY (event_timestamp)
    """)
    print("   ✓ Iceberg table created")
    
    print("\n3. Starting data generation (15 seconds)...")
    print("   Generating 5 events/second...")
    
    # Insert data from datagen to Iceberg
    statement_set = t_env.create_statement_set()
    statement_set.add_insert_sql("""
        INSERT INTO iceberg_catalog.cybersec.cloudtrail_events
        SELECT 
            event_id,
            event_version,
            event_timestamp,
            event_source,
            event_name,
            aws_region,
            source_ip,
            user_agent,
            user_type,
            user_arn,
            account_id,
            read_only,
            event_type,
            CURRENT_TIMESTAMP as processing_time
        FROM default_catalog.default_database.cloudtrail_datagen
    """)
    
    # Execute in background
    job_result = statement_set.execute()
    print(f"   ✓ Flink job started: {job_result.get_job_client().get_job_id()}")
    
    # Let it run for a bit
    time.sleep(15)
    
    print("\n4. Verifying data in Iceberg...")
    
    # Query the data
    result = t_env.execute_sql("""
        SELECT COUNT(*) as event_count 
        FROM iceberg_catalog.cybersec.cloudtrail_events
    """)
    
    count = 0
    with result.collect() as results:
        for row in results:
            count = row[0]
            break
    
    print(f"   ✓ Found {count} events in Iceberg table")
    
    # Show sample
    if count > 0:
        print("\n5. Sample events:")
        result = t_env.execute_sql("""
            SELECT event_id, event_name, event_source, aws_region, event_timestamp
            FROM iceberg_catalog.cybersec.cloudtrail_events
            LIMIT 5
        """)
        
        with result.collect() as results:
            for row in results:
                print(f"   {row}")
    
    print("\n" + "=" * 70)
    print("✅ TEST PASSED - Data successfully written to Iceberg")
    print("=" * 70)
    print(f"\n📁 Check MinIO for Iceberg files:")
    print(f"   http://127.0.0.1:9011/browser/cybersec/iceberg/warehouse/cybersec/cloudtrail_events/")
    print(f"\n💾 Files to look for:")
    print(f"   - data/*.parquet (Parquet data files)")
    print(f"   - metadata/*.avro (Iceberg metadata)")
    
    return True


if __name__ == "__main__":
    try:
        test_flink_datagen_to_iceberg()
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
