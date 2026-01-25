# Scan Memory Exhaustion

## Symptom

Browser OOM, 7GB+ memory usage, slow/timeout responses on `/api/events` or other table scan endpoints.

## Cause

PyIceberg `scan().to_pandas()` loads all matching rows into memory. Without a `limit` parameter, scanning a large table (420k+ records) causes memory exhaustion.

The issue was discovered when the iceberg_browser Flask process grew to 7GB+ RAM and crashed.

**Root Cause:** Missing `limit=N` parameter in PyIceberg scan calls.

## Observation

**Automation Level: A (Full)**

```python
import psutil

def check_memory():
    """Check browser process memory usage."""
    for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info.get('cmdline', []) or [])
            if 'iceberg_browser' in cmdline or 'flask' in cmdline:
                rss_mb = proc.info['memory_info'].rss / 1024 / 1024
                if rss_mb > 4000:  # 4GB
                    return "critical", f"Browser using {rss_mb:.0f}MB"
                elif rss_mb > 2000:  # 2GB
                    return "warning", f"Browser using {rss_mb:.0f}MB"
                return "ok", f"Memory OK: {rss_mb:.0f}MB"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return "skipped", "Browser process not found"
```

**Signals:**
- Browser process RSS > 2GB (warning) or > 4GB (critical)
- Response times on `/api/events` > 30 seconds
- Python process killed by OOM killer
- Logs showing "Killed" or memory errors

## Solution

**Automation Level: B (Partial - restart is disruptive)**

**Target State:** Browser memory usage < 1GB, all scans use `limit=N`.

**Remediation:**

1. Restart the browser process:
   ```bash
   pkill -f iceberg_browser
   uv run python iceberg_browser.py &
   ```

2. Verify scan limits are in place (`iceberg_browser.py`):
   ```python
   # Line 307: MAX_SCAN_ROWS = 10000
   scan = table.scan(limit=MAX_SCAN_ROWS)  # NOT scan().limit()
   ```

3. Check all scan endpoints have limits:
   - `/api/events` - MAX_SCAN_ROWS = 10000
   - `/api/fsn/aggregate` - FSN_SCAN_LIMIT = 50000
   - `/api/event/<id>` - limit = 50000
   - `/api/fsn/drilldown` - DRILLDOWN_LIMIT = 50000

**Prevention:**
- Always use `table.scan(limit=N)` not `table.scan().limit(N)`
- Monitor memory via `/api/health` endpoint
- Set up alerts for memory > 2GB

## FMEA Reference

| Field | Value |
|-------|-------|
| Failure Mode ID | ICE_001 |
| Base Severity | 8 |
| Base Occurrence | 4 |
| Base Detection | 3 |
| Base RPN | 96 |
| Tier | TIER_0 |
| Category | iceberg |
