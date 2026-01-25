"""
PyIceberg Writer for CloudTrail Events
Consumes processed CloudTrail events from Kafka and writes to Iceberg format

Requirements: 
- pyiceberg[s3fs,sql-postgres]>=0.10.0
- kafka-python (for Kafka consumer)
"""

from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField, StringType, BooleanType, TimestampType, LongType
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import DayTransform
from pyiceberg.table.sorting import SortOrder, SortField
from pyiceberg.transforms import IdentityTransform
import pyarrow as pa
import json
import os
from datetime import datetime

try:
    from kafka import KafkaConsumer
except ImportError:
    print("Warning: kafka-python not installed. Install with: pip install kafka-python")
    KafkaConsumer = None


class IcebergWriter:
    """Write CloudTrail events to Iceberg tables using PostgreSQL catalog"""
    
    def __init__(self, catalog_uri: str, warehouse_path: str):
        """
        Initialize the Iceberg writer
        
        Args:
            catalog_uri: PostgreSQL connection URI
            warehouse_path: S3/MinIO path for Iceberg warehouse
        """
        # Configure Iceberg catalog with PostgreSQL backend and S3 storage
        # Uses sql-postgres extra for PostgreSQL catalog support
        # Uses s3fs extra for S3-compatible storage (MinIO)
        self.catalog = load_catalog(
            "cybersec",
            **{
                "type": "sql",
                "uri": catalog_uri,
                "warehouse": warehouse_path,
                "s3.endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9010"),
                "s3.access-key-id": os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
                "s3.secret-access-key": os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
                "s3.path-style-access": "true",
                # Force use of s3fs for S3 operations
                "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO"
            }
        )
        
    def create_cloudtrail_table(self, namespace: str = "cybersec", table_name: str = "cloudtrail_events"):
        """Create Iceberg table for CloudTrail events if it doesn't exist"""
        
        # Define the schema for CloudTrail events (all fields nullable for flexibility)
        schema = Schema(
            NestedField(1, "event_id", StringType(), required=False),
            NestedField(2, "event_version", StringType(), required=False),
            NestedField(3, "event_timestamp", TimestampType(), required=False),
            NestedField(4, "event_source", StringType(), required=False),
            NestedField(5, "event_name", StringType(), required=False),
            NestedField(6, "aws_region", StringType(), required=False),
            NestedField(7, "source_ip", StringType(), required=False),
            NestedField(8, "user_agent", StringType(), required=False),
            NestedField(9, "user_type", StringType(), required=False),
            NestedField(10, "user_arn", StringType(), required=False),
            NestedField(11, "account_id", StringType(), required=False),
            NestedField(12, "read_only", BooleanType(), required=False),
            NestedField(13, "event_type", StringType(), required=False),
            NestedField(14, "processing_time", TimestampType(), required=False),
        )
        
        # Partition by day + region for efficient time-range and region queries
        # This enables partition pruning in the FSN visualization
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=3,  # event_timestamp
                field_id=1000,
                transform=DayTransform(),
                name="event_day"
            ),
            PartitionField(
                source_id=6,  # aws_region
                field_id=1001,
                transform=IdentityTransform(),
                name="region"
            )
        )
        
        # Sort by event_timestamp and event_id for better query performance
        sort_order = SortOrder(
            SortField(source_id=3, transform=IdentityTransform()),  # event_timestamp
            SortField(source_id=1, transform=IdentityTransform())   # event_id
        )
        
        try:
            # Create namespace if it doesn't exist
            try:
                self.catalog.create_namespace(namespace)
                print(f"Created namespace: {namespace}")
            except Exception as e:
                print(f"Namespace {namespace} already exists or error: {e}")
            
            # Create table
            table = self.catalog.create_table(
                identifier=f"{namespace}.{table_name}",
                schema=schema,
                partition_spec=partition_spec,
                sort_order=sort_order,
            )
            print(f"Created table: {namespace}.{table_name}")
            return table
        except Exception as e:
            print(f"Table {namespace}.{table_name} already exists or error: {e}")
            return self.catalog.load_table(f"{namespace}.{table_name}")
    
    def write_from_kafka(
        self,
        topic: str = "cloudtrail-parsed",
        bootstrap_servers: str = "localhost:9092",
        batch_size: int = 1000,
        namespace: str = "cybersec",
        table_name: str = "cloudtrail_events"
    ):
        """
        Consume CloudTrail events from Kafka and write to Iceberg
        
        Args:
            topic: Kafka topic to consume from
        if KafkaConsumer is None:
            raise ImportError("kafka-python is required. Install with: pip install kafka-python")
        
            bootstrap_servers: Kafka bootstrap servers
            batch_size: Number of records to batch before writing
            namespace: Iceberg namespace
            table_name: Iceberg table name
        """
        # Ensure table exists
        table = self.create_cloudtrail_table(namespace, table_name)
        
        # Create Kafka consumer
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='iceberg-writer',
            auto_offset_reset='latest',
            enable_auto_commit=True
        )
        
        print(f"Consuming from Kafka topic: {topic}")
        print(f"Writing to Iceberg table: {namespace}.{table_name}")
        
        batch = []
        
        try:
            for message in consumer:
                event = message.value
                
                # Convert event to PyArrow format
                record = {
                    'event_id': event.get('event_id', ''),
                    'event_version': event.get('event_version', ''),
                    'event_timestamp': datetime.fromisoformat(event.get('event_timestamp', datetime.now().isoformat()).replace('Z', '+00:00')),
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
                
                batch.append(record)
                
                # Write batch when it reaches the specified size
                if len(batch) >= batch_size:
                    self._write_batch(table, batch)
                    batch = []
                    print(f"Wrote batch of {batch_size} records to Iceberg")
                    
        except KeyboardInterrupt:
            print("Shutting down...")
            if batch:
                self._write_batch(table, batch)
                print(f"Wrote final batch of {len(batch)} records")
        finally:
            consumer.close()
    
    def _write_batch(self, table, batch):
        """Write a batch of records to Iceberg table using direct file write"""
        if not batch:
            return
            
        # Convert to PyArrow table
        pa_table = pa.Table.from_pylist(batch)
        
        # Write directly as Parquet file to MinIO
        import pyarrow.parquet as pq
        from datetime import datetime
        
        # Create a unique filename
        filename = f"data-{datetime.now().strftime('%Y%m%d-%H%M%S')}.parquet"
        filepath = f"{table.location()}/data/{filename}"
        
        # Use the catalog's file IO to write
        output_file = table.io.new_output(filepath)
        with output_file.create() as f:
            pq.write_table(pa_table, f)


def main():
    """Main entry point for the Iceberg writer"""
    
    # Configuration from environment variables
    catalog_uri = os.getenv(
        "ICEBERG_CATALOG_URI",
        "postgresql://postgres@localhost:5438/cybersec"
    )
    warehouse_path = os.getenv(
        "ICEBERG_WAREHOUSE",
        "s3://cybersec/iceberg/warehouse"
    )
    kafka_bootstrap = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS",
        "localhost:9092"
    )
    
    print("Initializing Iceberg Writer...")
    print(f"  Catalog URI: {catalog_uri}")
    print(f"  Warehouse: {warehouse_path}")
    print(f"  Kafka: {kafka_bootstrap}")
    
    writer = IcebergWriter(catalog_uri, warehouse_path)
    
    # Start consuming and writing
    writer.write_from_kafka(
        topic="cloudtrail-parsed",
        bootstrap_servers=kafka_bootstrap,
        batch_size=100  # Smaller batch for development
    )


if __name__ == "__main__":
    main()
