# Health Check System Implementation

## Summary

Added FMEA-based health diagnostics to the cybersec MCP stack. This system provides proactive detection of issues like the PyIceberg OOM that occurred with 420k records consuming 7GB+ RAM.

## New Files Created

### Core Health Module (`cybersec/health/`)

| File | Purpose |
|------|---------|
| `models.py` | FMEA data models: RPNScore, FailureMode, CheckResult, HealthReport, EscalationTier |
| `catalog.py` | 10 failure modes with RPN scores across iceberg/flink/infra/data categories |
| `runner.py` | Check execution engine with run_all, run_quick, run_single, diagnose |
| `checks/iceberg.py` | ICE_001-003, DATA_001 checks (memory, catalog, freshness, snapshots) |
| `checks/flink.py` | FLINK_001-003 checks (taskmanagers, jobs, checkpoints) |
| `checks/infra.py` | INFRA_001-003 checks (postgres, minio, polaris) |

### Heuristics KB (`heuristics/`)

| File | Description |
|------|-------------|
| `_index.md` | Overview of heuristics system |
| `iceberg/scan_memory_exhaustion.md` | ICE_001 - the OOM issue |
| `iceberg/catalog_unreachable.md` | ICE_002 |

## New MCP Tools

Added to `cybersec/mcp/server.py`:

```python
@mcp.tool()
async def bootstrap_health(category: Optional[str] = None, quick: bool = False) -> dict:
    """Run FMEA-based health diagnostics..."""

@mcp.tool()
async def bootstrap_diagnose(failure_mode_id: str) -> dict:
    """Get detailed diagnosis for a specific failure mode..."""

@mcp.tool()
async def bootstrap_fix(failure_mode_id: str, dry_run: bool = True) -> dict:
    """Attempt remediation for a failure mode based on escalation tier..."""
```

## FMEA Risk Priority Number (RPN)

RPN = Severity x Occurrence x Detection (1-1000)

| Tier | RPN Range | Action |
|------|-----------|--------|
| TIER_0 | 1-100 | Auto-remediate immediately |
| TIER_1 | 101-200 | Auto-remediate with agent validation |
| TIER_2 | 201-400 | Require user approval |
| MANUAL | 401-1000 | Human intervention required |

### Key Failure Modes

| ID | Name | RPN | Tier | Detects |
|----|------|-----|------|---------|
| ICE_001 | Scan Memory Exhaustion | 96 | TIER_0 | Process memory >2GB (warn) / >4GB (critical) |
| ICE_002 | Catalog Unreachable | 144 | TIER_1 | PyIceberg catalog connection failures |
| FLINK_001 | TaskManager Missing | 224 | TIER_2 | No TaskManagers registered |
| INFRA_001 | PostgreSQL Down | 320 | TIER_2 | Database connectivity |

## Testing

```bash
# Quick health check (4 critical checks)
uv run python -c "
import asyncio
from cybersec.health.runner import run_health_check
from cybersec.health.models import HealthContext
from cybersec.bootstrap import BootstrapService

service = BootstrapService()
config = service.get_config()
ctx = HealthContext(config=config, ...)
report = await run_health_check(ctx, quick=True)
print(report.to_dict())
"

# Full check (10 checks across all categories)
report = await run_health_check(ctx, quick=False)

# Diagnose specific failure mode
runner = get_runner()
result = await runner.diagnose('ICE_001', ctx)
```

## Sample Output

```
Status: healthy
Checks run: 10
Passed: 8
Issues: 0

Results:
  iceberg:
    [ok] Memory OK: 39MB
    [ok] Catalog OK: 1 namespace(s)
    [skipped] Table not found (no data yet)
  flink:
    [ok] TaskManagers OK: 1
    [ok] Jobs OK: 1 running
    [ok] Checkpoints OK: 4091 completed
  infra:
    [ok] PostgreSQL OK (port 5438)
    [ok] MinIO OK
    [ok] Polaris OK (15ms)
```

## Dependencies Added

- `psutil>=5.9.0` - Process memory monitoring for ICE_001 check

## Integration Points

1. **MCP Server**: New tools available when server restarts
2. **CLI**: Can add `cybersec health` command using these modules
3. **Web UI**: Can add `/health` endpoint to iceberg_browser.py
4. **Automation**: Quick checks suitable for periodic monitoring

## Design Decisions

1. **Adapted FMEA from gaius**: Copied models rather than importing to avoid dependency coupling
2. **Escalation tiers**: Based on RPN score for graduated remediation
3. **Quick vs Full checks**: Quick=4 critical infra checks, Full=10 checks across all categories
4. **Heuristics KB**: Markdown files for documentation, not dynamically loaded yet
