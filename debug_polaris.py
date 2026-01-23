
from pyiceberg.catalog import load_catalog
import json
from datetime import datetime

CATALOG_CONFIG = {
    "type": "rest",
    "uri": "http://localhost:8181/api/catalog",
    "credential": "admin:admin",
    "scope": "PRINCIPAL_ROLE:ALL",
    "warehouse": "cybersec",
    "s3.endpoint": "http://localhost:9010",
    "s3.path-style-access": "true",
    "s3.access-key-id": "minioadmin",
    "s3.secret-access-key": "minioadmin",
}

catalog = load_catalog("cybersec", **CATALOG_CONFIG)
table = catalog.load_table("default.cloudtrail_events")
metadata = table.metadata

print("Loading metadata properties...")
print(metadata.properties)

print("Loading schema...")
schema_fields = []
for field in table.schema().fields:
    schema_fields.append({
        "id": field.field_id,
        "name": field.name,
        "type": str(field.field_type),
        "required": field.required,
        "doc": field.doc
    })

print("Loading partitions...")
partitions = []
for field in table.spec().fields:
    partitions.append({
        "field_id": field.field_id,
        "source_id": field.source_id,
        "name": field.name,
        "transform": str(field.transform)
    })

print("Loading snapshots...")
snapshots = []
for s in metadata.snapshots[-50:]:
    snapshots.append({
        "snapshot_id": s.snapshot_id,
        "timestamp_ms": s.timestamp_ms,
        "timestamp": datetime.fromtimestamp(s.timestamp_ms / 1000).isoformat(),
        "manifest_list": s.manifest_list,
        "summary": dict(s.summary)
    })

print("Success!")
