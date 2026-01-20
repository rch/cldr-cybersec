"""
Test Flink DataGen -> JSON Files -> PyIceberg -> MinIO
This demonstrates the complete pipeline without requiring Flink-Iceberg connector
"""

from pyflink.table import EnvironmentSettings, StreamTableEnvironment
import time
import json
import os
from pathlib import Path

def test_flink_datagen():
    """Generate CloudTrail events using Flink DataGen and write to JSON files"""
    
    print("=" * 70)
    print("Step 1: Flink DataGen - Generate CloudTrail Events")
    print("=" * 70)
    
    # Create output directory
    output_dir = "/tmp/cloudtrail_events"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Create Table Environment
    env_settings = EnvironmentSettings.in_streaming_mode()
    t_env = StreamTableEnvironment.create(environment_settings=env_settings)
    
    # Configure for local execution
    t_env.get_config().set("parallelism.default", "1")
    
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
            'rows-per-second' = '10',
            'number-of-rows' = '50',
            'fields.event_id.kind' = 'random',
            'fields.event_id.length' = '32',
            'fields.event_version.kind' = 'sequence',
            'fields.event_version.start' = '1',
            'fields.event_version.end' = '1'
        )
    """)
    print("   ✓ DataGen source created (will generate 50 events)")
    
    print("\n2. Creating JSON file sink...")
    t_env.execute_sql(f"""
        CREATE TABLE cloudtrail_json_sink (
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
            'connector' = 'filesystem',
            'path' = 'file://{output_dir}',
            'format' = 'json'
        )
    """)
    print(f"   ✓ JSON sink created at {output_dir}")
    
    print("\n3. Starting data generation...")
    # Insert data from datagen to JSON files
    result = t_env.execute_sql("""
        INSERT INTO cloudtrail_json_sink
        SELECT * FROM cloudtrail_datagen
    """)
    
    # Wait for completion
    result.wait()
    print("   ✓ Data generation complete")
    
    # Count generated files (Flink creates part-* files)
    output_path = Path(output_dir)
    json_files = [f for f in output_path.glob("*") if f.is_file() and f.name.startswith("part-")]
    print(f"   ✓ Generated {len(json_files)} data file(s)")
    
    # Show file sizes
    for f in json_files[:3]:
        size = f.stat().st_size
        print(f"      - {f.name}: {size} bytes")
    
    return output_dir, json_files


def test_pyiceberg_write(json_files):
    """Read JSON files and write to Iceberg using PyIceberg"""
    
    print("\n" + "=" * 70)
    print("Step 2: PyIceberg - Write to MinIO")
    print("=" * 70)
    
    from iceberg_writer.cloudtrail_writer import IcebergWriter
    from datetime import datetime
    
    catalog_uri = "postgresql://localhost:5438/cybersec"
    warehouse_path = "s3://cybersec/iceberg/warehouse"
    
    print(f"\n1. Initializing Iceberg writer...")
    print(f"   Catalog: {catalog_uri}")
    print(f"   Warehouse: {warehouse_path}")
    
    writer = IcebergWriter(catalog_uri, warehouse_path)
    
    print(f"\n2. Creating Iceberg table...")
    table = writer.create_cloudtrail_table(
        namespace="cybersec",
        table_name="cloudtrail_events_test"
    )
    print(f"   ✓ Table: {table.location()}")
    
    print(f"\n3. Reading JSON files and converting to Iceberg format...")
    events = []
    for json_file in json_files:
        with open(json_file, 'r') as f:
            for line in f:
                if line.strip():
                    event = json.loads(line)
                    
                    # Parse timestamp
                    ts_str = event.get('event_timestamp', '')
                    try:
                        event_ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    except:
                        event_ts = datetime.now()
                    
                    record = {
                        'event_id': event.get('event_id', ''),
                        'event_version': event.get('event_version', ''),
                        'event_timestamp': event_ts,
                        'event_source': event.get('event_source', ''),
                        'event_name': event.get('event_name', ''),
                        'aws_region': event.get('aws_region', ''),
                        'source_ip': event.get('source_ip', ''),
                        'user_agent': event.get('user_agent', ''),
                        'user_type': event.get('user_type', ''),
                        'user_arn': event.get('user_arn', ''),
                        'account_id': event.get('account_id', ''),
                        'read_only': event.get('read_only', False),
                        'event_type': event.get('event_type', ''),
                        'processing_time': datetime.now()
                    }
                    events.append(record)
    
    print(f"   ✓ Loaded {len(events)} events from JSON files")
    
    print(f"\n4. Writing to Iceberg table...")
    writer._write_batch(table, events)
    print(f"   ✓ Wrote {len(events)} records to Iceberg")
    
    print(f"\n5. Verifying data...")
    scan = table.scan(limit=1000)
    df = scan.to_arrow()
    print(f"   ✓ Table now contains {df.num_rows} records")
    
    if df.num_rows > 0:
        print(f"\n6. Sample records:")
        import pandas as pd
        pd_df = df.to_pandas()
        print(pd_df[['event_id', 'event_name', 'event_source', 'event_timestamp']].head(5))
    
    return df.num_rows


def main():
    """Run the complete test"""
    print("\n🚀 Testing Flink DataGen -> PyIceberg -> MinIO Pipeline\n")
    
    try:
        # Step 1: Generate data with Flink
        output_dir, json_files = test_flink_datagen()
        
        if not json_files:
            print("\n❌ No JSON files generated!")
            return False
        
        # Step 2: Write to Iceberg
        record_count = test_pyiceberg_write(json_files)
        
        # Success
        print("\n" + "=" * 70)
        print("✅ PIPELINE TEST PASSED")
        print("=" * 70)
        print(f"\n📊 Results:")
        print(f"   - Flink generated: {len(json_files)} JSON file(s)")
        print(f"   - Iceberg stored: {record_count} records")
        print(f"\n📁 Verify in MinIO:")
        print(f"   http://127.0.0.1:9011/browser/cybersec/iceberg/warehouse/cybersec/cloudtrail_events_test/")
        print(f"\n💡 Look for:")
        print(f"   - data/*.parquet files (your CloudTrail events)")
        print(f"   - metadata/*.avro files (Iceberg table metadata)")
        
        # Cleanup
        print(f"\n🧹 Cleaning up temporary files...")
        import shutil
        shutil.rmtree(output_dir)
        print(f"   ✓ Removed {output_dir}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
