"""
Test script to verify Iceberg catalog and MinIO connectivity
"""

import os
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType, TimestampType
import pyarrow as pa

def test_iceberg_setup():
    """Test Iceberg catalog and table creation"""
    
    print("Testing Iceberg setup...")
    
    # Configuration
    catalog_uri = "postgresql://localhost:5438/cybersec"
    warehouse_path = "s3://cybersec/iceberg/warehouse"
    
    print(f"  Catalog URI: {catalog_uri}")
    print(f"  Warehouse: {warehouse_path}")
    
    try:
        # Load catalog
        catalog = load_catalog(
            "cybersec_test",
            **{
                "type": "sql",
                "uri": catalog_uri,
                "warehouse": warehouse_path,
                "s3.endpoint": "http://localhost:9010",
                "s3.access-key-id": "minioadmin",
                "s3.secret-access-key": "minioadmin",
                "s3.path-style-access": "true",
                "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO"
            }
        )
        print("  ✓ Catalog loaded successfully")
        
        # Create namespace
        try:
            catalog.create_namespace("test")
            print("  ✓ Created namespace 'test'")
        except Exception as e:
            print(f"  ℹ Namespace 'test' may already exist: {e}")
        
        # Define simple schema with nullable fields
        schema = Schema(
            NestedField(1, "id", StringType(), required=False),
            NestedField(2, "timestamp", TimestampType(), required=False),
            NestedField(3, "message", StringType(), required=False),
        )
        
        # Create table
        try:
            table = catalog.create_table(
                identifier="test.sample_table",
                schema=schema,
            )
            print("  ✓ Created table 'test.sample_table'")
            
            # Write sample data
            sample_data = pa.Table.from_pydict({
                'id': ['1', '2', '3'],
                'timestamp': [pa.scalar(1737388800000000, type=pa.timestamp('us')),
                             pa.scalar(1737388860000000, type=pa.timestamp('us')),
                             pa.scalar(1737388920000000, type=pa.timestamp('us'))],
                'message': ['test1', 'test2', 'test3']
            })
            
            table.append(sample_data)
            print("  ✓ Wrote 3 sample records to table")
            
            # Read back
            scan = table.scan(limit=10)
            df = scan.to_arrow()
            print(f"  ✓ Read back {df.num_rows} records from table")
            
            # List files in MinIO
            print("\n  Table location:", table.location())
            
        except Exception as e:
            print(f"  ℹ Table may already exist: {e}")
            # Try to load existing table
            table = catalog.load_table("test.sample_table")
            print(f"  ✓ Loaded existing table")
            print(f"  Table location: {table.location()}")
            scan = table.scan(limit=10)
            df = scan.to_arrow()
            print(f"  ✓ Table has {df.num_rows} records")
        
        print("\n✅ Iceberg setup test PASSED")
        print("\nYou can verify files in MinIO:")
        print("  http://127.0.0.1:9011/browser/cybersec/iceberg/warehouse/test/sample_table/")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    test_iceberg_setup()
