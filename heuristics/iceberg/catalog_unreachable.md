# Catalog Connection Failed

## Symptom

500 errors on `/api/tables`, `/api/events` returns "Table not found", catalog operations fail with connection errors.

## Cause

Polaris REST catalog is unreachable or credentials are invalid. This can happen when:
- Polaris service is not running
- Network connectivity issues
- Authentication credentials expired or incorrect
- Catalog namespace doesn't exist

## Observation

**Automation Level: A (Full)**

```python
def check_catalog():
    """Check Iceberg catalog connectivity."""
    try:
        from pyiceberg.catalog import load_catalog
        catalog = load_catalog("cybersec", type="rest",
                               uri="http://localhost:8181/api/catalog", ...)
        namespaces = catalog.list_namespaces()
        return "ok", f"Catalog OK: {len(namespaces)} namespace(s)"
    except Exception as e:
        return "critical", f"Catalog connection failed: {e}"
```

**Signals:**
- `catalog.list_namespaces()` throws exception
- HTTP 401/403 on Polaris API calls
- "Connection refused" errors in logs
- Polaris health endpoint returns non-200

## Solution

**Automation Level: B (Partial)**

**Target State:** Catalog accessible, namespaces and tables visible.

**Remediation:**

1. Check Polaris is running:
   ```bash
   curl http://localhost:8181/q/health/ready
   curl http://localhost:8182/q/health/ready  # Admin port
   ```

2. Verify credentials:
   ```bash
   echo $POLARIS_CLIENT_ID
   echo $POLARIS_CLIENT_SECRET
   # Default: admin/admin for POLARIS realm
   ```

3. Restart Polaris if needed:
   ```bash
   devenv tasks run restart:polaris
   ```

4. Re-bootstrap catalog:
   ```bash
   devenv tasks run polaris:init
   ```

**Prevention:**
- Use devenv process manager for auto-restart
- Monitor Polaris health endpoint
- Set up alerts for catalog connectivity

## FMEA Reference

| Field | Value |
|-------|-------|
| Failure Mode ID | ICE_002 |
| Base Severity | 9 |
| Base Occurrence | 3 |
| Base Detection | 2 |
| Base RPN | 54 |
| Tier | TIER_0 |
| Category | iceberg |
