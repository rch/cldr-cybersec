"""Iceberg health checks.

Checks for:
- ICE_001: Scan memory exhaustion
- ICE_002: Catalog connection failed
- ICE_003: Stale data
"""

import time
from datetime import datetime
from typing import Any

from ..models import CheckResult, HealthContext
from ..catalog import ICE_001, ICE_002, ICE_003


async def check_memory(ctx: HealthContext) -> CheckResult:
    """ICE_001: Check browser process memory usage.

    Monitors for memory exhaustion from unbounded PyIceberg scans.
    Thresholds: warning > 2GB, critical > 4GB
    """
    try:
        import psutil
    except ImportError:
        return CheckResult.error("psutil not installed")

    start = time.monotonic()

    for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info.get('cmdline', []) or [])
            if 'iceberg_browser' in cmdline or ('flask' in cmdline and 'iceberg' in cmdline.lower()):
                rss_mb = proc.info['memory_info'].rss / 1024 / 1024
                duration = int((time.monotonic() - start) * 1000)

                if rss_mb > 4000:  # 4GB - critical
                    rpn = ICE_001.calculate_rpn()
                    return CheckResult.critical(
                        f"Browser using {rss_mb:.0f}MB RAM",
                        failure_mode_id="ICE_001",
                        rpn=rpn,
                        remediation="Restart browser, verify scan limits",
                        pid=proc.info['pid'],
                        rss_mb=round(rss_mb),
                        duration_ms=duration,
                    )
                elif rss_mb > 2000:  # 2GB - warning
                    rpn = ICE_001.calculate_rpn()
                    return CheckResult.warning(
                        f"Browser using {rss_mb:.0f}MB RAM",
                        failure_mode_id="ICE_001",
                        rpn=rpn,
                        remediation="Monitor memory, consider restart",
                        pid=proc.info['pid'],
                        rss_mb=round(rss_mb),
                        duration_ms=duration,
                    )
                else:
                    result = CheckResult.ok(f"Memory OK: {rss_mb:.0f}MB", rss_mb=round(rss_mb))
                    result.duration_ms = duration
                    return result
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    duration = int((time.monotonic() - start) * 1000)
    result = CheckResult.skipped("Browser process not found")
    result.duration_ms = duration
    return result


async def check_catalog(ctx: HealthContext) -> CheckResult:
    """ICE_002: Check Iceberg catalog connectivity.

    Verifies connection to Polaris REST catalog.
    """
    start = time.monotonic()

    try:
        from pyiceberg.catalog import load_catalog

        catalog = load_catalog(
            "cybersec",
            **{
                "type": "rest",
                "uri": f"{ctx.polaris_url}/api/catalog",
                "credential": "admin:admin",
                "scope": "PRINCIPAL_ROLE:ALL",
                "warehouse": "cybersec",
                "s3.endpoint": ctx.minio_endpoint,
                "s3.region": "us-east-1",
                "s3.path-style-access": "true",
                "s3.access-key-id": "minioadmin",
                "s3.secret-access-key": "minioadmin",
            }
        )

        namespaces = catalog.list_namespaces()
        duration = int((time.monotonic() - start) * 1000)

        # Store catalog in context for other checks
        ctx.catalog = catalog

        result = CheckResult.ok(
            f"Catalog OK: {len(namespaces)} namespace(s)",
            namespaces=len(namespaces),
        )
        result.duration_ms = duration
        return result

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        rpn = ICE_002.calculate_rpn()
        return CheckResult.critical(
            f"Catalog connection failed: {e}",
            failure_mode_id="ICE_002",
            rpn=rpn,
            remediation="Check Polaris health, verify credentials",
            error=str(e),
            duration_ms=duration,
        )


async def check_data_freshness(ctx: HealthContext) -> CheckResult:
    """ICE_003: Check for stale data.

    Verifies latest snapshot is recent (< 30 min).
    """
    start = time.monotonic()

    if not ctx.catalog:
        return CheckResult.skipped("Catalog not available")

    try:
        # Try to load the cloudtrail_events table
        table = ctx.catalog.load_table("cybersec.cloudtrail_events")
        snapshots = table.metadata.snapshots

        if not snapshots:
            duration = int((time.monotonic() - start) * 1000)
            rpn = ICE_003.calculate_rpn()
            return CheckResult.warning(
                "No snapshots in table",
                failure_mode_id="ICE_003",
                rpn=rpn,
                remediation="Run the data pipeline to generate data",
                duration_ms=duration,
            )

        latest = snapshots[-1]
        latest_ts = latest.timestamp_ms / 1000
        age_minutes = (time.time() - latest_ts) / 60
        duration = int((time.monotonic() - start) * 1000)

        if age_minutes > 30:  # 30 minutes stale
            rpn = ICE_003.calculate_rpn()
            return CheckResult.warning(
                f"Data stale: last snapshot {age_minutes:.0f} minutes ago",
                failure_mode_id="ICE_003",
                rpn=rpn,
                remediation="Check Flink job status, verify pipeline",
                age_minutes=round(age_minutes, 1),
                snapshot_id=latest.snapshot_id,
                duration_ms=duration,
            )

        result = CheckResult.ok(
            f"Data fresh: {age_minutes:.1f} minutes old",
            age_minutes=round(age_minutes, 1),
            snapshot_id=latest.snapshot_id,
        )
        result.duration_ms = duration
        return result

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        # Table might not exist yet - this is not critical
        if "NoSuchTableError" in str(type(e).__name__) or "not found" in str(e).lower():
            result = CheckResult.skipped(f"Table not found: {e}")
            result.duration_ms = duration
            return result
        return CheckResult.error(f"Failed to check data freshness: {e}", duration_ms=duration)


async def check_snapshot_count(ctx: HealthContext) -> CheckResult:
    """DATA_001: Check for snapshot accumulation.

    Too many snapshots (>100) can slow metadata operations.
    """
    start = time.monotonic()

    if not ctx.catalog:
        return CheckResult.skipped("Catalog not available")

    try:
        table = ctx.catalog.load_table("cybersec.cloudtrail_events")
        snapshot_count = len(table.metadata.snapshots)
        duration = int((time.monotonic() - start) * 1000)

        if snapshot_count > 100:
            from ..catalog import DATA_001
            rpn = DATA_001.calculate_rpn()
            return CheckResult.warning(
                f"{snapshot_count} snapshots accumulated",
                failure_mode_id="DATA_001",
                rpn=rpn,
                remediation="Run snapshot expiration",
                snapshots=snapshot_count,
                duration_ms=duration,
            )

        result = CheckResult.ok(f"Snapshots OK: {snapshot_count}", snapshots=snapshot_count)
        result.duration_ms = duration
        return result

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        if "not found" in str(e).lower():
            return CheckResult.skipped("Table not found")
        return CheckResult.error(f"Failed to check snapshots: {e}", duration_ms=duration)


# Registry of all iceberg checks
CHECKS: dict[str, Any] = {
    "ICE_001": check_memory,
    "ICE_002": check_catalog,
    "ICE_003": check_data_freshness,
    "DATA_001": check_snapshot_count,
}
