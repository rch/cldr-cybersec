"""Schema optimization commands for unified command system.

Commands:
    /schema                        - Analyze all tables for optimization opportunities
    /schema <table>                - Analyze specific table
    /schema fix <table>            - Apply optimizations (terraform apply style)
    /schema fix <table> --dry-run  - Preview optimizations (terraform plan style)
    /schema compact <table>        - Run compaction only
    /schema expire <table>         - Expire old snapshots only
    /schema partition <table>      - Add/evolve partitioning
"""

from .parser import ParsedCommand, CommandResult
from .registry import register_command


# Catalog configuration - matches iceberg_browser.py
CATALOG_CONFIG = {
    "type": "rest",
    "uri": "http://localhost:8181/api/catalog",
    "credential": "admin:admin",
    "scope": "PRINCIPAL_ROLE:ALL",
    "warehouse": "cybersec",
    "s3.endpoint": "http://localhost:9010",
    "s3.region": "us-east-1",
    "s3.path-style-access": "true",
    "s3.access-key-id": "minioadmin",
    "s3.secret-access-key": "minioadmin",
}


async def cmd_schema(cmd: ParsedCommand) -> CommandResult:
    """Analyze Iceberg tables for optimization opportunities.

    Uses RETE rules to detect issues and recommend fixes.

    Args:
        [table]  Optional table name (default: all tables)

    Options:
        --json, -j  Output as JSON
    """
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError
    from ..rete.iceberg import IcebergOptimizer, TableStats
    from ..rete.facts import Fact

    try:
        catalog = load_catalog("cybersec", **CATALOG_CONFIG)
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to connect to catalog: {e}",
        )

    # Get table name from args or analyze all
    table_name = cmd.args[0] if cmd.args else None

    if table_name:
        tables = [table_name]
    else:
        # List all tables
        tables = []
        for ns in catalog.list_namespaces():
            ns_str = ".".join(ns) if isinstance(ns, tuple) else str(ns)
            for t in catalog.list_tables(ns_str):
                tbl_name = t[1] if isinstance(t, tuple) else str(t)
                tables.append(f"{ns_str}.{tbl_name}")

    if not tables:
        return CommandResult(
            success=True,
            data={"tables": [], "recommendations": []},
            formatted="No tables found in catalog.",
        )

    all_recommendations = []
    table_stats_list = []

    for full_table_name in tables:
        try:
            table = catalog.load_table(full_table_name)
            stats = _gather_table_stats(table, full_table_name)
            table_stats_list.append(stats)

            # Run RETE analysis
            optimizer = IcebergOptimizer()
            optimizer.add_table(stats)

            # Add query pattern assumptions
            optimizer.add_query_pattern(
                stats.table_name,
                filters_on_timestamp=True,
                uses_time_range=True,
            )

            # Add full scan fact if unpartitioned
            if not stats.partition_spec:
                optimizer.engine.assert_fact(Fact(
                    'query',
                    stats.table_name,
                    is_full_scan=True,
                    has_partition_pruning=False,
                ))

            recommendations = optimizer.analyze()
            for rec in recommendations:
                all_recommendations.append({
                    "table": full_table_name,
                    "priority": rec.priority,
                    "action_type": rec.action_type,
                    "description": rec.description,
                    "impact": rec.estimated_impact,
                    "command": rec.command,
                })

        except NoSuchTableError:
            return CommandResult(
                success=False,
                error=f"Table not found: {full_table_name}",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error=f"Error analyzing {full_table_name}: {e}",
            )

    # Sort by priority
    all_recommendations.sort(key=lambda r: -r["priority"])

    data = {
        "tables": [_stats_to_dict(s) for s in table_stats_list],
        "recommendations": all_recommendations,
        "summary": {
            "tables_analyzed": len(tables),
            "total_recommendations": len(all_recommendations),
            "critical": len([r for r in all_recommendations if r["priority"] >= 900]),
            "warnings": len([r for r in all_recommendations if 700 <= r["priority"] < 900]),
            "suggestions": len([r for r in all_recommendations if r["priority"] < 700]),
        },
    }

    formatted = _format_schema_analysis(data)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


async def cmd_schema_fix(cmd: ParsedCommand) -> CommandResult:
    """Apply schema optimizations to a table.

    Executes recommended optimizations in priority order.
    Use --dry-run to preview changes without applying.

    Args:
        <table>  Table name (e.g., default.cloudtrail_events)

    Options:
        --dry-run       Preview changes without applying
        --compact       Only run compaction
        --expire        Only expire snapshots
        --all           Apply all safe optimizations (default)
        --json, -j      Output as JSON
    """
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError

    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: table name (e.g., default.cloudtrail_events)",
        )

    table_name = cmd.args[0]
    dry_run = cmd.options.get("dry_run") or cmd.options.get("dry-run", False)
    only_compact = cmd.options.get("compact", False)
    only_expire = cmd.options.get("expire", False)

    try:
        catalog = load_catalog("cybersec", **CATALOG_CONFIG)
        table = catalog.load_table(table_name)
    except NoSuchTableError:
        return CommandResult(
            success=False,
            error=f"Table not found: {table_name}",
        )
    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to connect to catalog: {e}",
        )

    # Gather stats and run analysis
    stats = _gather_table_stats(table, table_name)

    from ..rete.iceberg import IcebergOptimizer
    from ..rete.facts import Fact

    optimizer = IcebergOptimizer()
    optimizer.add_table(stats)
    optimizer.add_query_pattern(
        stats.table_name,
        filters_on_timestamp=True,
        uses_time_range=True,
    )
    if not stats.partition_spec:
        optimizer.engine.assert_fact(Fact(
            'query', stats.table_name,
            is_full_scan=True, has_partition_pruning=False,
        ))

    recommendations = optimizer.analyze()

    # Filter by type if requested
    if only_compact:
        recommendations = [r for r in recommendations if r.action_type == "compaction"]
    elif only_expire:
        recommendations = [r for r in recommendations if r.action_type == "expire_snapshots"]

    # Sort by priority (highest first)
    recommendations.sort(key=lambda r: -r.priority)

    results = []
    for rec in recommendations:
        if rec.action_type == "alert":
            # Alerts are informational, not actionable
            results.append({
                "action": rec.action_type,
                "description": rec.description,
                "status": "info",
                "dry_run": dry_run,
            })
            continue

        if dry_run:
            results.append({
                "action": rec.action_type,
                "description": rec.description,
                "status": "would_apply",
                "command": rec.command,
                "impact": rec.estimated_impact,
                "dry_run": True,
            })
        else:
            # Execute the fix
            fix_result = await _execute_fix(table, rec)
            results.append({
                "action": rec.action_type,
                "description": rec.description,
                "status": "applied" if fix_result["success"] else "failed",
                "message": fix_result.get("message", ""),
                "error": fix_result.get("error"),
                "dry_run": False,
            })

    data = {
        "table": table_name,
        "dry_run": dry_run,
        "fixes": results,
        "summary": {
            "total": len(results),
            "applied": len([r for r in results if r["status"] == "applied"]),
            "failed": len([r for r in results if r["status"] == "failed"]),
            "would_apply": len([r for r in results if r["status"] == "would_apply"]),
            "info": len([r for r in results if r["status"] == "info"]),
        },
    }

    formatted = _format_schema_fix(data)

    return CommandResult(
        success=all(r["status"] != "failed" for r in results),
        data=data,
        formatted=formatted,
    )


async def cmd_schema_compact(cmd: ParsedCommand) -> CommandResult:
    """Run compaction on a table.

    Rewrites small files into larger, optimally-sized files.

    Args:
        <table>  Table name

    Options:
        --target-size <mb>  Target file size in MB (default: 128)
        --dry-run           Preview without executing
        --json, -j          Output as JSON
    """
    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: table name",
        )

    table_name = cmd.args[0]
    dry_run = cmd.options.get("dry_run") or cmd.options.get("dry-run", False)
    target_size = int(cmd.options.get("target_size") or cmd.options.get("target-size", 128))

    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError

    try:
        catalog = load_catalog("cybersec", **CATALOG_CONFIG)
        table = catalog.load_table(table_name)
    except NoSuchTableError:
        return CommandResult(success=False, error=f"Table not found: {table_name}")
    except Exception as e:
        return CommandResult(success=False, error=f"Catalog error: {e}")

    stats = _gather_table_stats(table, table_name)

    if dry_run:
        # Calculate expected outcome
        current_files = stats.file_count
        current_size_mb = stats.total_size_mb
        expected_files = max(1, current_size_mb // target_size)

        data = {
            "table": table_name,
            "dry_run": True,
            "current": {
                "file_count": current_files,
                "total_size_mb": current_size_mb,
                "avg_file_size_mb": stats.avg_file_size_mb,
            },
            "expected": {
                "file_count": expected_files,
                "target_file_size_mb": target_size,
            },
            "reduction": f"{current_files} -> {expected_files} files ({(1 - expected_files/current_files)*100:.1f}% reduction)" if current_files > 0 else "N/A",
        }
        formatted = _format_compact_preview(data)
    else:
        result = await _run_compaction(table, target_size)
        data = {
            "table": table_name,
            "dry_run": False,
            **result,
        }
        formatted = _format_compact_result(data)

    return CommandResult(
        success=data.get("success", True),
        data=data,
        formatted=formatted,
    )


async def cmd_schema_expire(cmd: ParsedCommand) -> CommandResult:
    """Expire old snapshots from a table.

    Removes snapshots older than the retention period.

    Args:
        <table>  Table name

    Options:
        --older-than <hours>  Expire snapshots older than N hours (default: 24)
        --keep <n>            Keep at least N snapshots (default: 10)
        --dry-run             Preview without executing
        --json, -j            Output as JSON
    """
    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: table name",
        )

    table_name = cmd.args[0]
    dry_run = cmd.options.get("dry_run") or cmd.options.get("dry-run", False)
    older_than_hours = int(cmd.options.get("older_than") or cmd.options.get("older-than", 24))
    keep_count = int(cmd.options.get("keep", 10))

    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError

    try:
        catalog = load_catalog("cybersec", **CATALOG_CONFIG)
        table = catalog.load_table(table_name)
    except NoSuchTableError:
        return CommandResult(success=False, error=f"Table not found: {table_name}")
    except Exception as e:
        return CommandResult(success=False, error=f"Catalog error: {e}")

    stats = _gather_table_stats(table, table_name)
    current_snapshots = stats.snapshot_count

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(hours=older_than_hours)

    # Count snapshots to expire
    snapshots = list(table.snapshots())
    to_expire = [s for s in snapshots if s.timestamp_ms < cutoff.timestamp() * 1000]
    # Keep at least keep_count
    if len(snapshots) - len(to_expire) < keep_count:
        to_expire = to_expire[:max(0, len(snapshots) - keep_count)]

    if dry_run:
        data = {
            "table": table_name,
            "dry_run": True,
            "current_snapshots": current_snapshots,
            "snapshots_to_expire": len(to_expire),
            "snapshots_to_keep": current_snapshots - len(to_expire),
            "cutoff": cutoff.isoformat(),
            "older_than_hours": older_than_hours,
        }
        formatted = _format_expire_preview(data)
    else:
        result = await _run_expire_snapshots(table, cutoff, keep_count)
        data = {
            "table": table_name,
            "dry_run": False,
            **result,
        }
        formatted = _format_expire_result(data)

    return CommandResult(
        success=data.get("success", True),
        data=data,
        formatted=formatted,
    )


# --- Helper Functions ---

def _gather_table_stats(table, full_table_name: str) -> "TableStats":
    """Gather statistics from an Iceberg table."""
    from ..rete.iceberg import TableStats

    # Parse namespace.table
    parts = full_table_name.split(".")
    namespace = parts[0] if len(parts) > 1 else "default"
    table_name = parts[-1]

    # Get snapshots
    snapshots = list(table.snapshots())
    snapshot_count = len(snapshots)

    # Get files from current snapshot
    current = table.current_snapshot()
    file_count = 0
    total_size = 0

    if current:
        scan = table.scan()
        files = list(scan.plan_files())
        file_count = len(files)
        total_size = sum(f.file.file_size_in_bytes for f in files)

    total_size_mb = total_size // (1024 * 1024)
    avg_file_size_mb = total_size_mb // file_count if file_count > 0 else 0

    # Get row count
    row_count = 0
    if current:
        try:
            # Try to get from metadata
            df = table.scan(limit=1).to_pandas()
            # Estimate from file count and average
            row_count = len(table.scan().to_pandas())
        except Exception:
            row_count = 0

    # Get partition spec
    spec = table.spec()
    partition_spec = ""
    partition_count = 1
    if not spec.is_unpartitioned():
        partition_spec = str(spec)
        # Count partitions would require scanning manifests
        partition_count = 1  # Simplified

    # Get sort order
    sort_order = table.sort_order()
    has_sort_order = not sort_order.is_unsorted

    return TableStats(
        table_name=table_name,
        namespace=namespace,
        row_count=row_count,
        file_count=file_count,
        total_size_mb=total_size_mb,
        avg_file_size_mb=avg_file_size_mb,
        partition_count=partition_count,
        partition_spec=partition_spec,
        snapshot_count=snapshot_count,
        has_sort_order=has_sort_order,
    )


def _stats_to_dict(stats: "TableStats") -> dict:
    """Convert TableStats to dictionary."""
    return {
        "name": f"{stats.namespace}.{stats.table_name}",
        "row_count": stats.row_count,
        "file_count": stats.file_count,
        "total_size_mb": stats.total_size_mb,
        "avg_file_size_mb": stats.avg_file_size_mb,
        "partition_spec": stats.partition_spec or "unpartitioned",
        "snapshot_count": stats.snapshot_count,
        "has_sort_order": stats.has_sort_order,
    }


async def cmd_schema_cloudtrail(cmd: ParsedCommand) -> CommandResult:
    """CloudTrail-specific schema analysis with production-scale optimization.

    Uses domain-specific rules for security investigation patterns:
    - Time-bounded queries (90%+ of security queries)
    - User-centric investigations
    - IP-based threat hunting
    - Error/anomaly detection

    Args:
        <table>  Table name

    Options:
        --goal <id>       Target optimization goal (default: production_ready)
        --what-if <spec>  Hypothetical: assume changes (key=value,key=value)
        --path            Show step-by-step optimization path
        --gaps            Show gap analysis for goal
        --json, -j        Output as JSON
    """
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError
    from ..rete.cloudtrail import CloudTrailOptimizer, CloudTrailTableStats
    import json as json_lib

    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: table name",
        )

    table_name = cmd.args[0]
    goal_id = cmd.options.get("goal", "production_ready")
    what_if_spec = cmd.options.get("what_if") or cmd.options.get("what-if", "")
    show_path = cmd.options.get("path", False)
    show_gaps = cmd.options.get("gaps", False)

    try:
        catalog = load_catalog("cybersec", **CATALOG_CONFIG)
        table = catalog.load_table(table_name)
    except NoSuchTableError:
        return CommandResult(success=False, error=f"Table not found: {table_name}")
    except Exception as e:
        return CommandResult(success=False, error=f"Catalog error: {e}")

    # Gather CloudTrail-specific statistics
    ct_stats = _gather_cloudtrail_stats(table, table_name)

    optimizer = CloudTrailOptimizer()
    optimizer.analyze(ct_stats)

    # Handle --what-if
    if what_if_spec:
        changes = _parse_what_if_spec(what_if_spec, ct_stats.table_name)
        explanation = optimizer.what_if(goal_id, changes)

        data = {
            "table": table_name,
            "mode": "what_if",
            "goal": goal_id,
            "hypothetical_changes": [f"{k}={v}" for k, v in changes],
            "conclusion": explanation.conclusion.value,
            "summary": explanation.summary,
            "steps": [
                {"step": s.step_number, "action": s.action, "description": s.description, "result": s.result}
                for s in explanation.steps
            ],
        }
        formatted = _format_cloudtrail_what_if(data, explanation)
        return CommandResult(success=True, data=data, formatted=formatted)

    # Handle --path
    if show_path:
        path = optimizer.get_optimization_path(goal_id)
        data = {
            "table": table_name,
            "goal": goal_id,
            "optimization_path": path,
        }
        formatted = _format_optimization_path(data)
        return CommandResult(success=True, data=data, formatted=formatted)

    # Handle --gaps
    if show_gaps:
        gaps = optimizer.analyze_gaps(goal_id)
        data = {
            "table": table_name,
            "goal": goal_id,
            "status": gaps.status.value,
            "missing_facts": gaps.missing_facts,
            "blocking_conditions": gaps.blocking_conditions,
            "acquisition_plan": gaps.acquisition_plan,
        }
        formatted = _format_cloudtrail_gaps(data)
        return CommandResult(success=True, data=data, formatted=formatted)

    # Default: full analysis with goals and recommendations
    recommendations = optimizer.get_recommendations()
    goals = optimizer.list_goals()

    data = {
        "table": table_name,
        "stats": {
            "events_per_day": ct_stats.events_per_day,
            "total_events": ct_stats.total_events,
            "days_of_data": ct_stats.days_of_data,
            "unique_users": ct_stats.unique_users,
            "unique_ips": ct_stats.unique_ips,
            "error_rate": ct_stats.error_rate,
            "has_time_partition": ct_stats.has_time_partition,
            "denormalized_fields": ct_stats.denormalized_fields,
            "file_count": ct_stats.file_count,
            "snapshot_count": ct_stats.snapshot_count,
        },
        "goals": goals,
        "recommendations": recommendations,
    }

    formatted = _format_cloudtrail_analysis(data, ct_stats)

    return CommandResult(success=True, data=data, formatted=formatted)


def _gather_cloudtrail_stats(table, full_table_name: str) -> "CloudTrailTableStats":
    """Gather CloudTrail-specific statistics from a table."""
    from ..rete.cloudtrail import CloudTrailTableStats
    import json as json_lib
    from collections import Counter

    parts = full_table_name.split(".")
    namespace = parts[0] if len(parts) > 1 else "default"
    table_name = parts[-1]

    # Basic stats
    snapshots = list(table.snapshots())
    snapshot_count = len(snapshots)

    current = table.current_snapshot()
    file_count = 0
    total_size = 0

    if current:
        scan = table.scan()
        files = list(scan.plan_files())
        file_count = len(files)
        total_size = sum(f.file.file_size_in_bytes for f in files)

    # Get schema info
    schema = table.schema()
    denormalized = []
    has_json_blob = False
    for field in schema.fields:
        if field.name == "event_data":
            has_json_blob = True
        elif field.name in ["event_name", "event_source", "aws_region", "user_arn", "source_ip", "error_code"]:
            denormalized.append(field.name)

    # Partition info
    spec = table.spec()
    has_time_partition = False
    partition_granularity = ""
    if not spec.is_unpartitioned():
        for pf in spec.fields:
            if "hour" in str(pf).lower():
                has_time_partition = True
                partition_granularity = "hour"
            elif "day" in str(pf).lower():
                has_time_partition = True
                partition_granularity = "day"

    # Sort order
    sort_order = table.sort_order()
    sort_columns = []
    if not sort_order.is_unsorted:
        sort_columns = [str(f) for f in sort_order.fields]

    # Sample data for cardinality estimates
    try:
        df = table.scan(limit=5000).to_pandas()
        total_events = len(df)

        # Parse JSON to get cardinalities
        unique_users = set()
        unique_ips = set()
        unique_sources = set()
        error_count = 0

        for _, row in df.iterrows():
            if "event_data" in row and row["event_data"]:
                try:
                    event = json_lib.loads(row["event_data"])
                    ui = event.get("userIdentity", {})
                    unique_users.add(ui.get("arn", ui.get("userName", "")))
                    unique_ips.add(event.get("sourceIPAddress", ""))
                    unique_sources.add(event.get("eventSource", ""))
                    if event.get("errorCode"):
                        error_count += 1
                except:
                    pass

        # Estimate full table metrics
        if snapshot_count > 0:
            # Rough estimation based on sample
            scale_factor = snapshot_count / max(1, len(df) / 100)  # Assume ~100 events per snapshot
            estimated_total = total_events * scale_factor

        # Time range
        if "event_time" in df.columns:
            time_range = df["event_time"].max() - df["event_time"].min()
            days_of_data = max(1, time_range.days if hasattr(time_range, 'days') else 1)
        else:
            days_of_data = 1

        events_per_day = total_events // max(1, days_of_data)

    except Exception:
        total_events = 0
        events_per_day = 0
        days_of_data = 1
        unique_users = set()
        unique_ips = set()
        unique_sources = set()
        error_count = 0

    return CloudTrailTableStats(
        table_name=table_name,
        namespace=namespace,
        events_per_day=events_per_day,
        total_events=total_events,
        total_size_gb=total_size / (1024**3),
        days_of_data=days_of_data,
        is_partitioned=not spec.is_unpartitioned(),
        has_time_partition=has_time_partition,
        partition_granularity=partition_granularity,
        denormalized_fields=denormalized,
        has_json_blob=has_json_blob,
        sort_columns=sort_columns,
        file_count=file_count,
        avg_file_size_mb=total_size // (1024 * 1024 * max(1, file_count)),
        snapshot_count=snapshot_count,
        unique_users=len(unique_users),
        unique_ips=len(unique_ips),
        unique_event_sources=len(unique_sources),
        error_rate=(error_count / max(1, total_events)) * 100,
    )


def _parse_what_if_spec(spec: str, table_name: str) -> list[tuple[str, any]]:
    """Parse what-if specification into fact changes.

    Maps user-friendly keys to the appropriate fact types:
    - has_time_partition -> cloudtrail_table.X.has_time_partition
    - has_user_column -> schema_state.X.has_user_column
    - avg_file_size_mb -> cloudtrail_table.X.avg_file_size_mb
    - snapshot_count -> cloudtrail_table.X.snapshot_count
    """
    # Map keys to their fact types
    cloudtrail_table_attrs = {
        "has_time_partition", "events_per_day", "total_events",
        "days_of_data", "file_count", "avg_file_size_mb", "snapshot_count",
        "unique_users", "unique_ips", "error_rate",
    }
    schema_state_attrs = {
        "has_user_column", "has_ip_column", "has_error_column",
        "has_event_source_column", "has_region_column",
        "is_sorted", "is_z_ordered",
    }

    changes = []
    for item in spec.split(","):
        if "=" in item:
            key, value = item.strip().split("=", 1)
            # Convert value
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.isdigit():
                value = int(value)

            # Build fact pattern with correct fact type
            if "." not in key:
                if key in cloudtrail_table_attrs:
                    pattern = f"cloudtrail_table.{table_name}.{key}"
                elif key in schema_state_attrs:
                    pattern = f"schema_state.{table_name}.{key}"
                else:
                    # Default to schema_state
                    pattern = f"schema_state.{table_name}.{key}"
            else:
                pattern = key

            changes.append((pattern, value))

    return changes


def _format_cloudtrail_analysis(data: dict, stats) -> str:
    """Format CloudTrail analysis for human display."""
    lines = []
    lines.append("CloudTrail Schema Analysis")
    lines.append("=" * 60)
    lines.append(f"Table: {data['table']}")
    lines.append("")

    # Stats summary
    s = data["stats"]
    lines.append("Current State:")
    lines.append(f"  Events/day: {s['events_per_day']:,}")
    lines.append(f"  Total events: {s['total_events']:,}")
    lines.append(f"  Days of data: {s['days_of_data']}")
    lines.append(f"  Files: {s['file_count']:,} | Snapshots: {s['snapshot_count']:,}")
    lines.append("")
    lines.append("  Cardinality:")
    lines.append(f"    Users: {s['unique_users']} | IPs: {s['unique_ips']} | Error rate: {s['error_rate']:.1f}%")
    lines.append("")
    lines.append("  Schema:")
    lines.append(f"    Time partitioned: {'Yes (' + stats.partition_granularity + ')' if s['has_time_partition'] else 'No'}")
    lines.append(f"    Denormalized fields: {s['denormalized_fields'] or 'None (JSON blob only)'}")
    lines.append("")

    # Goals status
    lines.append("Optimization Goals:")
    lines.append("-" * 40)
    for g in data["goals"]:
        icon = {"proven": "✓", "disproven": "✗", "unknown": "○"}.get(g["status"], "?")
        lines.append(f"  {icon} {g['goal_id']}: {g['description']}")
    lines.append("")

    # Recommendations
    if data["recommendations"]:
        lines.append("Recommendations:")
        lines.append("-" * 40)
        for rec in data["recommendations"][:5]:  # Top 5
            sev_icon = {"critical": "✗", "warning": "⚠", "info": "ℹ"}.get(rec["severity"], "•")
            lines.append(f"  {sev_icon} [{rec['priority']}] {rec['category'].upper()}")
            lines.append(f"      {rec['description'][:70]}...")
        lines.append("")

    # What-if hints
    lines.append("Try what-if analysis:")
    lines.append("  /schema cloudtrail <table> --what-if has_time_partition=true")
    lines.append("  /schema cloudtrail <table> --path")
    lines.append("  /schema cloudtrail <table> --gaps")

    return "\n".join(lines)


def _format_cloudtrail_what_if(data: dict, explanation) -> str:
    """Format what-if analysis for CloudTrail."""
    lines = []
    lines.append("CloudTrail What-If Analysis")
    lines.append("=" * 60)
    lines.append(f"Table: {data['table']}")
    lines.append(f"Goal: {data['goal']}")
    lines.append("")
    lines.append(f"Hypothetical changes: {', '.join(data['hypothetical_changes'])}")
    lines.append("")

    icon = {"proven": "✓", "disproven": "✗", "unknown": "?"}.get(data["conclusion"], "?")
    lines.append(f"Result: {icon} {data['conclusion'].upper()}")
    lines.append(f"Summary: {data['summary']}")
    lines.append("")

    lines.append("Reasoning:")
    for step in data["steps"]:
        if step["result"]:
            result_icon = "✓" if "SATISFIED" in step["result"] else "✗" if "FAILED" in step["result"] else "?"
            lines.append(f"  {step['step']}. {step['description']}")
            lines.append(f"       {result_icon} {step['result']}")
        else:
            lines.append(f"  {step['step']}. {step['description']}")

    return "\n".join(lines)


def _format_optimization_path(data: dict) -> str:
    """Format optimization path."""
    lines = []
    lines.append("CloudTrail Optimization Path")
    lines.append("=" * 60)
    lines.append(f"Table: {data['table']}")
    lines.append(f"Target: {data['goal']}")
    lines.append("")

    path = data["optimization_path"]
    if path and path[0].get("status") == "achieved":
        lines.append("✓ Goal already achieved!")
    else:
        lines.append("Steps to achieve goal:")
        lines.append("-" * 40)
        for step in path:
            lines.append(f"  {step['step']}. {step['action']}")
            lines.append(f"       {step['description']}")
            lines.append(f"       Cost: {step['cost']}")
            lines.append("")

    return "\n".join(lines)


def _format_cloudtrail_gaps(data: dict) -> str:
    """Format gap analysis for CloudTrail."""
    lines = []
    lines.append("CloudTrail Gap Analysis")
    lines.append("=" * 60)
    lines.append(f"Table: {data['table']}")
    lines.append(f"Goal: {data['goal']}")
    lines.append("")

    icon = {"proven": "✓", "disproven": "✗", "unknown": "?"}.get(data["status"], "?")
    lines.append(f"Status: {icon} {data['status'].upper()}")
    lines.append("")

    if data["missing_facts"]:
        lines.append(f"Missing ({len(data['missing_facts'])}):")
        for mf in data["missing_facts"]:
            lines.append(f"  • {mf}")
        lines.append("")

    if data["blocking_conditions"]:
        lines.append("Blocking conditions:")
        for bc in data["blocking_conditions"]:
            lines.append(f"  ✗ {bc['pattern']}: expected {bc['expected']}, got {bc.get('actual', 'unknown')}")
        lines.append("")

    if data["acquisition_plan"]:
        lines.append("Acquisition plan (by cost):")
        for i, acq in enumerate(data["acquisition_plan"], 1):
            lines.append(f"  {i}. {acq['fact_pattern']} (cost: {acq['cost']})")
            lines.append(f"       {acq['action']}")

    return "\n".join(lines)


async def _execute_fix(table, recommendation) -> dict:
    """Execute a single optimization fix."""
    try:
        if recommendation.action_type == "compaction":
            return await _run_compaction(table, target_size_mb=128)
        elif recommendation.action_type == "expire_snapshots":
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=1)
            return await _run_expire_snapshots(table, cutoff, keep_count=10)
        elif recommendation.action_type == "rewrite_manifests":
            return await _run_rewrite_manifests(table)
        else:
            return {"success": False, "error": f"Unknown action type: {recommendation.action_type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _run_compaction(table, target_size_mb: int = 128) -> dict:
    """Run compaction on a table."""
    try:
        # PyIceberg doesn't have direct compaction API in older versions
        # We'll use rewrite_data_files if available, otherwise report manual steps
        if hasattr(table, 'rewrite_data_files'):
            table.rewrite_data_files(
                target_file_size_bytes=target_size_mb * 1024 * 1024,
            )
            return {
                "success": True,
                "message": f"Compaction completed with target size {target_size_mb}MB",
            }
        else:
            # Provide Spark SQL command as fallback
            return {
                "success": False,
                "error": "PyIceberg compaction not available in this version",
                "manual_command": f"""
-- Run in Spark SQL:
CALL catalog.system.rewrite_data_files(
    table => '{table.name()}',
    options => map('target-file-size-bytes', '{target_size_mb * 1024 * 1024}')
)
""",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _run_expire_snapshots(table, cutoff, keep_count: int = 10) -> dict:
    """Expire old snapshots."""
    try:
        if hasattr(table, 'expire_snapshots'):
            # Get snapshot count before
            before_count = len(list(table.snapshots()))

            table.expire_snapshots().older_than(cutoff).retain_last(keep_count).commit()

            after_count = len(list(table.snapshots()))
            expired = before_count - after_count

            return {
                "success": True,
                "message": f"Expired {expired} snapshots",
                "before": before_count,
                "after": after_count,
            }
        else:
            return {
                "success": False,
                "error": "Snapshot expiration not available",
                "manual_command": f"""
-- Run in Spark SQL:
CALL catalog.system.expire_snapshots(
    table => '{table.name()}',
    older_than => TIMESTAMP '{cutoff.isoformat()}',
    retain_last => {keep_count}
)
""",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _run_rewrite_manifests(table) -> dict:
    """Rewrite manifest files."""
    try:
        if hasattr(table, 'rewrite_manifests'):
            table.rewrite_manifests().commit()
            return {"success": True, "message": "Manifests rewritten"}
        else:
            return {
                "success": False,
                "error": "Manifest rewriting not available in this PyIceberg version",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Formatting Functions ---

def _format_schema_analysis(data: dict) -> str:
    """Format schema analysis for human display."""
    lines = []
    lines.append("Schema Optimization Analysis")
    lines.append("=" * 50)
    lines.append("")

    summary = data["summary"]
    lines.append(f"Tables analyzed: {summary['tables_analyzed']}")
    lines.append(f"Total recommendations: {summary['total_recommendations']}")
    if summary["critical"]:
        lines.append(f"  Critical: {summary['critical']}")
    if summary["warnings"]:
        lines.append(f"  Warnings: {summary['warnings']}")
    if summary["suggestions"]:
        lines.append(f"  Suggestions: {summary['suggestions']}")
    lines.append("")

    # Table stats
    for tbl in data["tables"]:
        lines.append(f"Table: {tbl['name']}")
        lines.append(f"  Rows: {tbl['row_count']:,} | Files: {tbl['file_count']:,} | Size: {tbl['total_size_mb']} MB")
        lines.append(f"  Partitioning: {tbl['partition_spec']}")
        lines.append(f"  Snapshots: {tbl['snapshot_count']:,}")
        lines.append("")

    # Recommendations
    if data["recommendations"]:
        lines.append("Recommendations (by priority):")
        lines.append("-" * 40)

        for rec in data["recommendations"]:
            severity = "CRITICAL" if rec["priority"] >= 900 else "WARNING" if rec["priority"] >= 700 else "INFO"
            icon = {"CRITICAL": "✗", "WARNING": "⚠", "INFO": "ℹ"}.get(severity, "•")
            lines.append(f"  {icon} [{rec['priority']}] {rec['action_type'].upper()}")
            lines.append(f"      {rec['description']}")
            if rec.get("impact"):
                lines.append(f"      Impact: {rec['impact']}")
            lines.append("")

        lines.append("To apply fixes:")
        lines.append("  /schema fix <table>            # Apply all")
        lines.append("  /schema fix <table> --dry-run  # Preview first")
        lines.append("  /schema compact <table>        # Compaction only")
        lines.append("  /schema expire <table>         # Expire snapshots only")

    return "\n".join(lines)


def _format_schema_fix(data: dict) -> str:
    """Format schema fix results."""
    lines = []

    if data["dry_run"]:
        lines.append("Schema Fix (DRY RUN - Preview Only)")
    else:
        lines.append("Schema Fix (Applying Changes)")
    lines.append("=" * 50)
    lines.append(f"Table: {data['table']}")
    lines.append("")

    for fix in data["fixes"]:
        if data["dry_run"]:
            if fix["status"] == "would_apply":
                lines.append(f"  + {fix['action'].upper()}: {fix['description'][:60]}...")
                if fix.get("impact"):
                    lines.append(f"      Impact: {fix['impact']}")
            elif fix["status"] == "info":
                lines.append(f"  ℹ {fix['action'].upper()}: {fix['description'][:60]}...")
        else:
            icon = "✓" if fix["status"] == "applied" else "✗" if fix["status"] == "failed" else "ℹ"
            lines.append(f"  {icon} {fix['action'].upper()}: {fix['description'][:60]}...")
            if fix.get("message"):
                lines.append(f"      {fix['message']}")
            if fix.get("error"):
                lines.append(f"      Error: {fix['error']}")
        lines.append("")

    summary = data["summary"]
    if data["dry_run"]:
        lines.append(f"Plan: {summary['would_apply']} fix(es) would be applied")
        lines.append("")
        lines.append("To apply:")
        lines.append(f"  /schema fix {data['table']}")
    else:
        lines.append(f"Applied: {summary['applied']}/{summary['total']}")
        if summary["failed"]:
            lines.append(f"Failed: {summary['failed']}")

    return "\n".join(lines)


def _format_compact_preview(data: dict) -> str:
    """Format compaction preview."""
    lines = []
    lines.append("Compaction Preview (DRY RUN)")
    lines.append("=" * 50)
    lines.append(f"Table: {data['table']}")
    lines.append("")
    lines.append("Current state:")
    lines.append(f"  Files: {data['current']['file_count']:,}")
    lines.append(f"  Total size: {data['current']['total_size_mb']} MB")
    lines.append(f"  Avg file size: {data['current']['avg_file_size_mb']} MB")
    lines.append("")
    lines.append("After compaction:")
    lines.append(f"  Files: ~{data['expected']['file_count']}")
    lines.append(f"  Target file size: {data['expected']['target_file_size_mb']} MB")
    lines.append("")
    lines.append(f"Reduction: {data['reduction']}")
    lines.append("")
    lines.append("To apply:")
    lines.append(f"  /schema compact {data['table']}")
    return "\n".join(lines)


def _format_compact_result(data: dict) -> str:
    """Format compaction result."""
    lines = []
    lines.append("Compaction Result")
    lines.append("=" * 50)
    lines.append(f"Table: {data['table']}")
    lines.append("")
    if data.get("success"):
        lines.append(f"✓ {data.get('message', 'Compaction completed')}")
    else:
        lines.append(f"✗ {data.get('error', 'Compaction failed')}")
        if data.get("manual_command"):
            lines.append("")
            lines.append("Manual command:")
            lines.append(data["manual_command"])
    return "\n".join(lines)


def _format_expire_preview(data: dict) -> str:
    """Format snapshot expiration preview."""
    lines = []
    lines.append("Snapshot Expiration Preview (DRY RUN)")
    lines.append("=" * 50)
    lines.append(f"Table: {data['table']}")
    lines.append("")
    lines.append(f"Current snapshots: {data['current_snapshots']:,}")
    lines.append(f"Snapshots to expire: {data['snapshots_to_expire']:,}")
    lines.append(f"Snapshots to keep: {data['snapshots_to_keep']:,}")
    lines.append(f"Cutoff: {data['cutoff']} ({data['older_than_hours']}h ago)")
    lines.append("")
    lines.append("To apply:")
    lines.append(f"  /schema expire {data['table']}")
    return "\n".join(lines)


def _format_expire_result(data: dict) -> str:
    """Format snapshot expiration result."""
    lines = []
    lines.append("Snapshot Expiration Result")
    lines.append("=" * 50)
    lines.append(f"Table: {data['table']}")
    lines.append("")
    if data.get("success"):
        lines.append(f"✓ {data.get('message', 'Snapshots expired')}")
        if "before" in data:
            lines.append(f"  Before: {data['before']:,} snapshots")
            lines.append(f"  After: {data['after']:,} snapshots")
    else:
        lines.append(f"✗ {data.get('error', 'Expiration failed')}")
        if data.get("manual_command"):
            lines.append("")
            lines.append("Manual command:")
            lines.append(data["manual_command"])
    return "\n".join(lines)


# === Register commands ===

def register_schema_commands():
    """Register all schema commands."""
    register_command(
        "schema",
        cmd_schema,
        description="Analyze Iceberg tables for optimization opportunities",
        args=[
            {"name": "table", "required": False, "description": "Table name (omit for all tables)"},
        ],
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/schema",
            "/schema default.cloudtrail_events",
            "/schema --json",
        ],
    )

    register_command(
        "schema.fix",
        cmd_schema_fix,
        description="Apply schema optimizations (like terraform apply)",
        args=[
            {"name": "table", "required": True, "description": "Table name"},
        ],
        options=[
            {"name": "dry-run", "description": "Preview changes without applying"},
            {"name": "compact", "description": "Only run compaction"},
            {"name": "expire", "description": "Only expire snapshots"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/schema fix default.cloudtrail_events",
            "/schema fix default.cloudtrail_events --dry-run",
            "/schema fix default.cloudtrail_events --compact",
        ],
    )

    register_command(
        "schema.compact",
        cmd_schema_compact,
        description="Run compaction on a table",
        args=[
            {"name": "table", "required": True, "description": "Table name"},
        ],
        options=[
            {"name": "target-size", "description": "Target file size in MB (default: 128)"},
            {"name": "dry-run", "description": "Preview without executing"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/schema compact default.cloudtrail_events",
            "/schema compact default.cloudtrail_events --dry-run",
            "/schema compact default.cloudtrail_events --target-size 256",
        ],
    )

    register_command(
        "schema.expire",
        cmd_schema_expire,
        description="Expire old snapshots from a table",
        args=[
            {"name": "table", "required": True, "description": "Table name"},
        ],
        options=[
            {"name": "older-than", "description": "Expire snapshots older than N hours (default: 24)"},
            {"name": "keep", "description": "Keep at least N snapshots (default: 10)"},
            {"name": "dry-run", "description": "Preview without executing"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/schema expire default.cloudtrail_events",
            "/schema expire default.cloudtrail_events --dry-run",
            "/schema expire default.cloudtrail_events --older-than 48 --keep 5",
        ],
    )

    register_command(
        "schema.cloudtrail",
        cmd_schema_cloudtrail,
        description="CloudTrail-specific schema analysis with what-if",
        args=[
            {"name": "table", "required": True, "description": "Table name"},
        ],
        options=[
            {"name": "goal", "description": "Target goal (default: production_ready)"},
            {"name": "what-if", "description": "Hypothetical changes (comma-separated key=value)"},
            {"name": "path", "description": "Show optimization path to goal"},
            {"name": "gaps", "description": "Show gap analysis for goal"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/schema cloudtrail default.cloudtrail_events",
            "/schema cloudtrail default.cloudtrail_events --goal production_ready",
            "/schema cloudtrail default.cloudtrail_events --what-if has_time_partition=true",
            "/schema cloudtrail default.cloudtrail_events --path",
            "/schema cloudtrail default.cloudtrail_events --gaps",
        ],
    )
