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
from urllib.parse import urlparse

app = Flask(__name__)


# ============================================================================
# Dynamic Host Detection for External Links
# ============================================================================
# When accessed via FQDN (e.g., Cloudflare WARP), adjust service links accordingly.

# Service port mappings - maps service name to its port
SERVICE_PORTS = {
    "flink": 8081,
    "minio_console": 9011,
    "minio_api": 9010,
    "polaris_api": 8181,
    "polaris_admin": 8182,
    "nifi": 8450,
    "prometheus": 9090,
    "iceberg_browser": 5050,
    "otel_grpc": 4317,
    "otel_http": 4318,
    "otel_metrics": 8889,
}


def get_base_host():
    """
    Detect if we're being accessed via FQDN or localhost.
    Returns the base hostname without port.
    """
    # Check X-Forwarded-Host first (for reverse proxies like Cloudflare)
    forwarded_host = request.headers.get("X-Forwarded-Host")
    if forwarded_host:
        # Extract just the hostname (might be "host:port")
        return forwarded_host.split(":")[0]

    # Fall back to Host header
    host = request.headers.get("Host", "localhost:5050")
    return host.split(":")[0]


def get_service_url(service_name: str, path: str = "") -> str:
    """
    Generate a URL for a service based on the current request context.

    If accessed via localhost, returns localhost URLs.
    If accessed via FQDN, returns FQDN URLs with appropriate ports.

    Args:
        service_name: Name of the service (e.g., "flink", "minio_console")
        path: Optional path to append (e.g., "/api/health")

    Returns:
        Full URL string like "http://localhost:8081" or "http://myhost.example.com:8081"
    """
    base_host = get_base_host()
    port = SERVICE_PORTS.get(service_name, 80)

    # Determine protocol - assume HTTP for local dev
    # Could be extended to check X-Forwarded-Proto for HTTPS
    proto = request.headers.get("X-Forwarded-Proto", "http")

    # Build the URL
    if port == 80:
        url = f"{proto}://{base_host}"
    else:
        url = f"{proto}://{base_host}:{port}"

    if path:
        url = f"{url}{path}"

    return url


def get_all_service_urls() -> dict:
    """
    Get URLs for all services, adjusted for the current request context.

    Returns:
        Dict mapping service names to their full URLs
    """
    return {
        "flink": get_service_url("flink"),
        "flink_ui": get_service_url("flink", "/#/overview"),
        "minio_console": get_service_url("minio_console"),
        "minio_api": get_service_url("minio_api"),
        "polaris_api": get_service_url("polaris_api"),
        "polaris_admin": get_service_url("polaris_admin"),
        "nifi": get_service_url("nifi", "/nifi"),
        "prometheus": get_service_url("prometheus"),
        "iceberg_browser": get_service_url("iceberg_browser"),
    }


@app.context_processor
def inject_service_urls():
    """Make service URLs available to all templates."""
    return {
        "service_urls": get_all_service_urls(),
        "get_service_url": get_service_url,
    }

# Iceberg catalog configuration - using REST catalog to connect to Polaris
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
        # Try to load cloudtrail_events from default namespace
        try:
            return catalog.load_table("default.cloudtrail_events")
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
    except Exception as e:
        print(f"Error loading table: {e}")
        import traceback
        traceback.print_exc()
        return None


@app.route("/")
def index():
    """Main page"""
    return render_template("index.html")


@app.route("/api/service-urls")
def api_service_urls():
    """
    Get service URLs adjusted for the current request context.

    When accessed via localhost, returns localhost URLs.
    When accessed via FQDN (e.g., through Cloudflare WARP), returns FQDN URLs.

    Returns:
        JSON object with service names mapped to their URLs
    """
    return jsonify({
        "base_host": get_base_host(),
        "services": get_all_service_urls(),
        "ports": SERVICE_PORTS,
    })


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
        
        # Process events: parse JSON and handle types
        processed_events = []
        for event in events:
            # Parse event_data JSON if it exists
            if "event_data" in event and event["event_data"]:
                try:
                    event_data_str = event["event_data"]
                    if isinstance(event_data_str, str):
                        event_data = json.loads(event_data_str)
                        
                        # Flatten common fields for frontend
                        # Map CloudTrail fields to what frontend expects
                        if "eventName" in event_data:
                            event["event_name"] = event_data["eventName"]
                        if "userIdentity" in event_data:
                            userIdentity = event_data["userIdentity"]
                            if isinstance(userIdentity, dict):
                                if "userName" in userIdentity:
                                    event["user_identity"] = userIdentity["userName"]
                                elif "arn" in userIdentity:
                                    # Extract user from ARN
                                    event["user_identity"] = userIdentity["arn"].split("/")[-1]
                        if "sourceIPAddress" in event_data:
                            event["source_ip_address"] = event_data["sourceIPAddress"]
                        if "awsRegion" in event_data:
                            event["region"] = event_data["awsRegion"]
                        if "errorCode" in event_data:
                            event["error_code"] = event_data["errorCode"]
                        if "errorMessage" in event_data:
                            event["error_message"] = event_data["errorMessage"]
                            
                        # Merge the rest
                        event.update(event_data)
                except Exception as e:
                    print(f"Error parsing event_data: {e}")
            
            # Convert any datetime objects to ISO format
            for key, value in event.items():
                if isinstance(value, pd.Timestamp):
                    event[key] = value.isoformat()
                elif pd.isna(value):
                    event[key] = None
            
            processed_events.append(event)
            
        events = processed_events
        
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
        
        # Parse event_data JSON if it exists
        if "event_data" in event and event["event_data"]:
            try:
                event_data_str = event["event_data"]
                if isinstance(event_data_str, str):
                    event_data = json.loads(event_data_str)
                    
                    # Flatten common fields for frontend
                    if "eventName" in event_data:
                        event["event_name"] = event_data["eventName"]
                    if "userIdentity" in event_data:
                        userIdentity = event_data["userIdentity"]
                        if isinstance(userIdentity, dict):
                            if "userName" in userIdentity:
                                event["user_identity"] = userIdentity["userName"]
                            elif "arn" in userIdentity:
                                event["user_identity"] = userIdentity["arn"].split("/")[-1]
                    if "sourceIPAddress" in event_data:
                        event["source_ip_address"] = event_data["sourceIPAddress"]
                    if "awsRegion" in event_data:
                        event["region"] = event_data["awsRegion"]
                        
                    # Merge the rest
                    event.update(event_data)
            except Exception as e:
                print(f"Error parsing event_data: {e}")
        
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


# Store metrics for change detection and rate calculation
_last_snapshot_id = None
_last_event_count = 0
_event_timestamps = []  # Track event times for rate calculation


def get_table_changes():
    """Check if table has changed and calculate events per minute"""
    global _last_snapshot_id, _last_event_count, _event_timestamps
    
    try:
        table = get_table()
        if not table:
            return None
        
        metadata = table.metadata
        current_snapshot_id = metadata.current_snapshot_id
        
        # Get current count
        scan = table.scan()
        df = scan.to_pandas()
        current_count = len(df)
        
        # Calculate new events
        new_events = current_count - _last_event_count
        
        # Update tracking
        if new_events > 0:
            now = time.time()
            # Add timestamp for each new event
            _event_timestamps.extend([now] * new_events)
            _last_event_count = current_count
            _last_snapshot_id = current_snapshot_id
        
        # Calculate events per minute (last 60 seconds)
        now = time.time()
        cutoff = now - 60
        _event_timestamps[:] = [ts for ts in _event_timestamps if ts > cutoff]
        events_per_minute = len(_event_timestamps)
        
        return {
            "total_events": current_count,
            "events_per_minute": events_per_minute,
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
                    # Send metrics update (total events and events per minute)
                    yield f"data: {json.dumps({'type': 'metrics', 'data': changes})}\n\n"
                
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



@app.route("/polaris")
def polaris_page():
    """Polaris Insights page"""
    return render_template("polaris.html")


# ============================================================================
# Bootstrap / Settings Routes
# ============================================================================


@app.route("/settings")
def settings_page():
    """Bootstrap settings page"""
    return render_template("settings.html")


@app.route("/api/bootstrap/info")
def bootstrap_info():
    """Get bootstrap configuration and status"""
    try:
        from cybersec.bootstrap import BootstrapService
        service = BootstrapService()
        config = service.get_config()

        return jsonify({
            "config_file": str(service.settings.config_path),
            "config_exists": service.settings.exists(),
            "bootstrap_completed": config.completed,
            "last_run": config.last_run,
            "paths": {
                "flink_home": str(config.get_flink_home()) if config.get_flink_home() else None,
                "flink_state": str(config.get_flink_state_dir()),
                "minio_data": str(config.get_minio_data_dir()),
                "log_dir": str(config.get_log_dir()),
            },
            "services": {
                "postgres": {"host": config.postgres_host, "port": config.postgres_port},
                "polaris": {"api_url": config.polaris_api_url, "admin_url": config.polaris_admin_url},
                "flink": {"url": config.flink_url},
                "minio": {"endpoint": config.minio_endpoint, "console": config.minio_console},
                "iceberg_browser": {"port": config.iceberg_browser_port},
            },
            "catalog": {
                "name": config.catalog_name,
                "warehouse": config.catalog_warehouse,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bootstrap/status")
def bootstrap_status():
    """Get service health status"""
    import asyncio
    try:
        from cybersec.bootstrap import BootstrapService
        service = BootstrapService()

        # Run async health checks
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(service.check_all_services())
        finally:
            loop.close()

        all_healthy = all(r.get("healthy", False) for r in results)

        return jsonify({
            "all_healthy": all_healthy,
            "services": results,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bootstrap/settings", methods=["GET", "POST"])
def bootstrap_settings():
    """Get or update bootstrap settings"""
    try:
        from cybersec.bootstrap import BootstrapService, BootstrapConfig
        service = BootstrapService()

        if request.method == "POST":
            data = request.get_json() or {}

            if data.get("reset"):
                config = BootstrapConfig()
                service.settings.save(config)
                return jsonify({"action": "reset", "message": "Settings reset to defaults"})

            if data.get("updates"):
                service.update_config(**data["updates"])
                return jsonify({
                    "action": "updated",
                    "message": f"Updated {len(data['updates'])} setting(s)",
                })

        return jsonify({"config": service.get_config_dict()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bootstrap/verify")
def bootstrap_verify():
    """Verify bootstrap configuration"""
    import asyncio
    try:
        from cybersec.bootstrap import BootstrapService
        service = BootstrapService()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(service.verify())
        finally:
            loop.close()

        return jsonify({
            "all_passed": result.get("all_passed", False),
            "checks": result.get("checks", []),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bootstrap/assess")
def bootstrap_assess():
    """Quick assessment for startup check"""
    import asyncio
    try:
        from cybersec.bootstrap import BootstrapService
        service = BootstrapService()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(service.assess())
        finally:
            loop.close()

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bootstrap/run", methods=["POST"])
def bootstrap_run():
    """Execute bootstrap (returns SSE stream)"""
    import asyncio
    from cybersec.bootstrap import BootstrapService, EventType

    data = request.get_json() or {}
    skip_flink = data.get("skip_flink", False)
    flink_path = data.get("flink_path")
    dry_run = data.get("dry_run", False)

    def generate():
        service = BootstrapService()

        async def run_bootstrap():
            async for event in service.run(
                skip_flink=skip_flink,
                flink_path=flink_path,
                dry_run=dry_run,
            ):
                event_data = {
                    "type": event.event_type.value,
                    "task_id": event.task_id,
                    "message": event.message,
                    "progress": event.progress,
                }

                # Include prompt options if present
                if event.prompt_options:
                    event_data["prompt_options"] = [
                        {"key": o.key, "label": o.label, "description": o.description, "default": o.default}
                        for o in event.prompt_options
                    ]
                    event_data["prompt_allow_custom"] = event.prompt_allow_custom

                yield f"data: {json.dumps(event_data)}\n\n"

        # Run the async generator
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Convert async generator to sync
            async def collect_events():
                events = []
                async for event in service.run(
                    skip_flink=skip_flink,
                    flink_path=flink_path,
                    dry_run=dry_run,
                ):
                    events.append(event)
                return events

            events = loop.run_until_complete(collect_events())
            for event in events:
                event_data = {
                    "type": event.event_type.value,
                    "task_id": event.task_id,
                    "message": event.message,
                    "progress": event.progress,
                }
                if event.prompt_options:
                    event_data["prompt_options"] = [
                        {"key": o.key, "label": o.label, "description": o.description, "default": o.default}
                        for o in event.prompt_options
                    ]
                    event_data["prompt_allow_custom"] = event.prompt_allow_custom
                yield f"data: {json.dumps(event_data)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            loop.close()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


@app.route("/api/catalog-info")
def get_catalog_info():
    """Get catalog configuration and status"""
    try:
        catalog = get_catalog()
        
        # Get structure
        structure = {}
        namespaces = catalog.list_namespaces()
        total_tables = 0
        
        for namespace in namespaces:
            namespace_str = ".".join(namespace)
            tables = catalog.list_tables(namespace_str)
            table_names = [t[1] if isinstance(t, tuple) else str(t) for t in tables]
            structure[namespace_str] = table_names
            total_tables += len(tables)
            
        # Check health (internal probe to localhost:8182)
        import urllib.request
        health_status = "DOWN"
        try:
            with urllib.request.urlopen("http://localhost:8182/q/health", timeout=2) as response:
                if response.getcode() == 200:
                    health_data = json.loads(response.read())
                    health_status = health_data.get("status", "UNKNOWN")
        except Exception as e:
            print(f"Health check failed: {e}")
            
        return jsonify({
            "config": {
                "uri": CATALOG_CONFIG["uri"],
                "warehouse": CATALOG_CONFIG["warehouse"],
                "scope": CATALOG_CONFIG["scope"],
                "s3_endpoint": CATALOG_CONFIG["s3.endpoint"],
                "properties": catalog.properties
            },
            "status": health_status,
            "structure": structure,
            "total_tables": total_tables
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/catalog/namespace/<path:namespace>")
def get_namespace_details(namespace):
    """Get namespace properties"""
    try:
        catalog = get_catalog()
        # Create namespace tuple/string
        ns_parts = namespace.split(".")
        if len(ns_parts) == 1:
            ns_idf = ns_parts[0]
        else:
            ns_idf = tuple(ns_parts)
            
        props = catalog.load_namespace_properties(ns_idf)
        return jsonify({"namespace": namespace, "properties": props})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/catalog/table/<path:table_name>")
def get_table_details(table_name):
    """Get detailed table information"""
    try:
        catalog = get_catalog()
        table = catalog.load_table(table_name)
        metadata = table.metadata
        
        # Schema
        schema_fields = []
        for field in table.schema().fields:
            schema_fields.append({
                "id": field.field_id,
                "name": field.name,
                "type": str(field.field_type),
                "required": field.required,
                "doc": field.doc
            })
            
        # Partition Spec
        partitions = []
        for field in table.spec().fields:
            partitions.append({
                "field_id": field.field_id,
                "source_id": field.source_id,
                "name": field.name,
                "transform": str(field.transform)
            })
            
        # Snapshots (Limit to last 50 for performance)
        snapshots = []
        for s in metadata.snapshots[-50:]:
            snapshots.append({
                "snapshot_id": s.snapshot_id,
                "timestamp_ms": s.timestamp_ms,
                "timestamp": datetime.fromtimestamp(s.timestamp_ms / 1000).isoformat(),
                "manifest_list": s.manifest_list,
                "summary": dict(s.summary)
            })
            
        # Reverse snapshots to show newest first
        snapshots.reverse()
            
        return jsonify({
            "identifier": str(table_name),
            "properties": metadata.properties,
            "schema": schema_fields,
            "partitions": partitions,
            "snapshots": snapshots,
            "location": metadata.location,
            "current_snapshot_id": metadata.current_snapshot_id,
            "format_version": metadata.format_version
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



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
