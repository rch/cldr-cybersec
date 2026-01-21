"""
Iceberg Browser - Web UI for CloudTrail Events

A simple Flask web application to browse and search CloudTrail events stored in Iceberg.
Supports real-time updates via Server-Sent Events (SSE).
"""

from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
import pandas as pd
from datetime import datetime, timedelta
import os
import time
import json
import threading

app = Flask(__name__)

# Iceberg catalog configuration - using SQL catalog (PostgreSQL)
# This avoids OAuth complexity and works reliably with Polaris
CATALOG_CONFIG = {
    "type": "sql",
    "uri": "postgresql://cybersec:cybersec@localhost:5438/iceberg",
    "warehouse": "s3://cybersec/iceberg/warehouse",
    "s3.endpoint": "http://localhost:9010",
    "s3.path-style-access": "true",
    "s3.access-key-id": "minioadmin",
    "s3.secret-access-key": "minioadmin",
    "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO"
}

# Global catalog instance (singleton to avoid re-initialization)
_catalog = None


def get_catalog():
    """Get Iceberg catalog instance"""
    global _catalog
    if _catalog is None:
        _catalog = load_catalog("cybersec", **CATALOG_CONFIG)
    return _catalog


def get_table():
    """Get the first available table in the catalog"""
    try:
        catalog = get_catalog()
        # Try to load cloudtrail_events first
        try:
            return catalog.load_table("cybersec.cloudtrail_events")
        except NoSuchTableError:
            pass
        
        # Otherwise, find the first available table
        namespaces = catalog.list_namespaces()
        for namespace in namespaces:
            tables = catalog.list_tables(namespace)
            if tables:
                # Return the first table found
                return catalog.load_table(tables[0])
        
        return None
    except Exception:
        return None


@app.route("/")
def index():
    """Main page"""
    return render_template("index.html")


@app.route("/api/tables")
def list_tables():
    """List all tables in the catalog"""
    try:
        catalog = get_catalog()
        namespaces = catalog.list_namespaces()
        
        tables = []
        for namespace in namespaces:
            namespace_str = ".".join(namespace)
            table_list = catalog.list_tables(namespace_str)
            for table_id in table_list:
                tables.append({
                    "namespace": namespace_str,
                    "name": table_id[1] if isinstance(table_id, tuple) else str(table_id),
                    "full_name": f"{namespace_str}.{table_id[1] if isinstance(table_id, tuple) else str(table_id)}"
                })
        
        return jsonify({"tables": tables})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/schema")
def get_schema():
    """Get table schema"""
    try:
        table = get_table()
        if not table:
            return jsonify({"error": "Table not found"}), 404
        
        schema = table.schema()
        fields = []
        for field in schema.fields:
            fields.append({
                "id": field.field_id,
                "name": field.name,
                "type": str(field.field_type),
                "required": field.required,
            })
        
        return jsonify({
            "schema_id": schema.schema_id,
            "fields": fields
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
def get_stats():
    """Get table statistics"""
    try:
        table = get_table()
        if not table:
            return jsonify({"error": "Table not found"}), 404
        
        # Get metadata
        metadata = table.metadata
        snapshots = metadata.snapshots
        
        # Get actual table identifier - handle tuple format
        try:
            if isinstance(table.identifier, tuple):
                table_name = ".".join(table.identifier)
            else:
                table_name = str(table.identifier)
        except:
            table_name = "unknown"
        
        stats = {
            "table_name": table_name,
            "format_version": metadata.format_version,
            "location": metadata.location,
            "snapshot_count": len(snapshots),
            "current_snapshot_id": metadata.current_snapshot_id,
        }
        
        if snapshots:
            current_snapshot = metadata.snapshot_by_id(metadata.current_snapshot_id)
            if current_snapshot:
                stats["last_updated"] = datetime.fromtimestamp(
                    current_snapshot.timestamp_ms / 1000
                ).isoformat()
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/events")
def get_events():
    """Query CloudTrail events with optional filters"""
    try:
        table = get_table()
        if not table:
            return jsonify({"error": "Table not found"}), 404
        
        # Parse query parameters
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        event_name = request.args.get("event_name")
        user_identity = request.args.get("user_identity")
        source_ip = request.args.get("source_ip")
        region = request.args.get("region")
        
        # Start with table scan
        scan = table.scan()
        
        # Apply filters if provided
        # Note: PyIceberg filter syntax may need adjustment based on version
        # For now, we'll fetch data and filter with pandas
        
        # Convert to pandas DataFrame
        df = scan.to_pandas()
        
        # Apply filters
        if event_name:
            df = df[df["event_name"].str.contains(event_name, case=False, na=False)]
        if user_identity:
            df = df[df["user_identity"].str.contains(user_identity, case=False, na=False)]
        if source_ip:
            df = df[df["source_ip_address"] == source_ip]
        if region:
            df = df[df["region"] == region]
        
        # Get total count before pagination
        total_count = len(df)
        
        # Sort by timestamp descending
        if "event_time" in df.columns:
            df = df.sort_values("event_time", ascending=False)
        
        # Apply pagination
        df = df.iloc[offset:offset + limit]
        
        # Convert to records
        events = df.to_dict(orient="records")
        
        # Convert any datetime objects to ISO format
        for event in events:
            for key, value in event.items():
                if isinstance(value, pd.Timestamp):
                    event[key] = value.isoformat()
                elif pd.isna(value):
                    event[key] = None
        
        return jsonify({
            "events": events,
            "total": total_count,
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/event/<event_id>")
def get_event_detail(event_id):
    """Get detailed information for a specific event"""
    try:
        table = get_table()
        if not table:
            return jsonify({"error": "Table not found"}), 404
        
        scan = table.scan()
        df = scan.to_pandas()
        
        # Find event by ID
        event_df = df[df["event_id"] == event_id]
        
        if event_df.empty:
            return jsonify({"error": "Event not found"}), 404
        
        event = event_df.iloc[0].to_dict()
        
        # Convert datetime objects
        for key, value in event.items():
            if isinstance(value, pd.Timestamp):
                event[key] = value.isoformat()
            elif pd.isna(value):
                event[key] = None
        
        return jsonify(event)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/summary")
def get_summary():
    """Get summary statistics and aggregations"""
    try:
        table = get_table()
        if not table:
            return jsonify({"error": "Table not found"}), 404
        
        scan = table.scan()
        df = scan.to_pandas()
        
        summary = {
            "total_events": len(df),
        }
        
        # Add column-specific summaries based on what columns exist
        if "event_name" in df.columns:
            summary["event_names"] = df["event_name"].value_counts().head(10).to_dict()
        if "region" in df.columns:
            summary["regions"] = df["region"].value_counts().to_dict()
        if "user_identity" in df.columns:
            summary["users"] = df["user_identity"].value_counts().head(10).to_dict()
        if "source_ip_address" in df.columns:
            summary["source_ips"] = df["source_ip_address"].value_counts().head(10).to_dict()
        if "name" in df.columns:
            summary["names"] = df["name"].value_counts().head(10).to_dict()
        if "amount" in df.columns:
            summary["amount_stats"] = {
                "mean": float(df["amount"].mean()),
                "min": int(df["amount"].min()),
                "max": int(df["amount"].max()),
                "total": int(df["amount"].sum())
            }
        
        # Time range
        if "event_time" in df.columns and not df["event_time"].empty:
            summary["earliest_event"] = df["event_time"].min().isoformat()
            summary["latest_event"] = df["event_time"].max().isoformat()
        
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Store last known snapshot ID for change detection
_last_snapshot_id = None
_last_event_count = 0


def get_table_changes():
    """Check if table has changed since last check"""
    global _last_snapshot_id, _last_event_count
    
    try:
        table = get_table()
        if not table:
            return None
        
        metadata = table.metadata
        current_snapshot_id = metadata.current_snapshot_id
        
        # Check if snapshot changed
        if current_snapshot_id != _last_snapshot_id:
            _last_snapshot_id = current_snapshot_id
            
            # Get latest stats
            scan = table.scan()
            df = scan.to_pandas()
            current_count = len(df)
            
            new_events = current_count - _last_event_count
            _last_event_count = current_count
            
            # Get latest events if new data exists
            latest_events = []
            if new_events > 0 and not df.empty:
                if "event_time" in df.columns:
                    df = df.sort_values("event_time", ascending=False)
                latest_df = df.head(min(5, new_events))
                
                for _, row in latest_df.iterrows():
                    event = row.to_dict()
                    for key, value in event.items():
                        if isinstance(value, pd.Timestamp):
                            event[key] = value.isoformat()
                        elif pd.isna(value):
                            event[key] = None
                    latest_events.append(event)
            
            return {
                "snapshot_id": current_snapshot_id,
                "total_events": current_count,
                "new_events": new_events,
                "latest_events": latest_events,
                "timestamp": datetime.now().isoformat()
            }
    except Exception as e:
        return {"error": str(e)}
    
    return None


@app.route("/api/stream")
def stream_updates():
    """Server-Sent Events endpoint for real-time updates"""
    def generate():
        # Send initial connection message
        yield f"data: {json.dumps({'type': 'connected', 'message': 'Connected to live updates'})}\n\n"
        
        while True:
            try:
                # Check for changes every 2 seconds
                changes = get_table_changes()
                
                if changes:
                    # Send update event
                    yield f"data: {json.dumps({'type': 'update', 'data': changes})}\n\n"
                
                # Send heartbeat every iteration
                yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
                
                time.sleep(2)
            except GeneratorExit:
                break
            except Exception as e:
                error_data = {'type': 'error', 'message': str(e)}
                yield f"data: {json.dumps(error_data)}\n\n"
                time.sleep(5)
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


if __name__ == "__main__":
    # Create templates directory if it doesn't exist
    os.makedirs("templates", exist_ok=True)
    
    print("=" * 60)
    print("Iceberg Browser - CloudTrail Events UI")
    print("=" * 60)
    print("Starting web server on http://localhost:5050")
    print("Make sure the following services are running:")
    print("  - PostgreSQL (localhost:5438)")
    print("  - MinIO (localhost:9010)")
    print("=" * 60)
    
    app.run(host="0.0.0.0", port=5050, debug=True)
