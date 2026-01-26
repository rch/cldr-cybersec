# FSN Iceberg Optimization Mode - Implementation Summary

## Overview

Added a new "Iceberg Optimization" mode to the FSN 3D visualization that helps cluster operators understand and act on schema optimization recommendations from the RETE/OR-Tools framework.

## What Was Implemented

### 1. Backend API Endpoints (`iceberg_browser.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fsn/iceberg/tables` | GET | Returns all tables with optimization metrics for FSN visualization |
| `/api/fsn/iceberg/partitions/<table>` | GET | Returns per-partition metrics for drill-down visualization |
| `/api/fsn/iceberg/recommendations` | GET | Returns RETE recommendations for all tables |
| `/api/fsn/iceberg/apply` | POST | Executes an optimization fix (compaction, expire snapshots, etc.) |
| `/api/fsn/iceberg/what-if` | POST | Preview fix outcome using RETE what-if analysis |
| `/api/fsn/settings` | GET/POST | FSN visualization settings |

### 2. FSN Mode Toggle (`templates/index.html`)

- Added dropdown in FSN header to switch between "CloudTrail Events" and "Iceberg Optimization" modes
- Mode toggle persists preference to settings
- Switching mode reloads visualization with appropriate data

### 3. Iceberg Visualization

**Axis Mapping:**
- X-axis: Position in grid
- Z-axis: Position in grid (tables laid out in square grid)
- Y-axis (height): File count (higher = more fragmentation)
- Color: Health score (red=critical, orange=warning, green=healthy)
- Wireframe: Added to blocks with snapshot explosion (>50 snapshots)

**Health Score Calculation:**
```
health_score = (
    file_size_score * 0.3 +    # avg_file_size vs 128MB target
    file_count_score * 0.3 +   # fewer files = better
    snapshot_score * 0.2 +     # <50 snapshots = better
    partition_score * 0.2      # partitioned = better
)
```

### 4. Recommendations Panel

- Shows top 5 recommendations from RETE analysis
- Each recommendation has:
  - Rule ID and table name
  - Description of the issue
  - Preview button (dry-run analysis)
  - Apply button (execute fix)

### 5. Settings Integration (`templates/settings.html`, `cybersec/bootstrap/config.py`)

New FSN settings:
- `fsn_default_mode`: "cloudtrail" | "iceberg"
- `fsn_remember_mode`: Remember mode between sessions
- `fsn_iceberg_auto_refresh`: Auto-refresh optimization data
- `fsn_iceberg_refresh_interval`: Refresh interval in seconds

## Testing

APIs verified working:

```bash
# Get table metrics
curl http://localhost:5050/api/fsn/iceberg/tables
# Returns: tables with health_score, file_count, recommendations

# Get recommendations
curl http://localhost:5050/api/fsn/iceberg/recommendations
# Returns: prioritized list of optimization recommendations

# Preview a fix
curl -X POST http://localhost:5050/api/fsn/iceberg/apply \
  -H "Content-Type: application/json" \
  -d '{"table": "default.cloudtrail_events", "action": "compact", "dry_run": true}'
# Returns: preview of compaction (current_files -> expected_files)

# Get FSN settings
curl http://localhost:5050/api/fsn/settings
# Returns: {default_mode, remember_mode, iceberg_auto_refresh, iceberg_refresh_interval}
```

## User Workflow

1. **Mode Selection**: Click dropdown in FSN header to switch to "Iceberg Optimization"
2. **Visual Assessment**: See red/yellow/green blocks representing table health
3. **Inspect Block**: Click block to see details (file count, snapshots, recommendations)
4. **Preview Fix**: Click "Preview" on a recommendation to see what would change
5. **Apply Fix**: Click "Apply" to execute the optimization
6. **Verify**: Watch the block animate toward green as health improves

## Files Modified

1. `iceberg_browser.py` - Added 6 new API endpoints + helper functions
2. `templates/index.html` - Mode toggle, CSS, Iceberg visualization JS, tooltips
3. `templates/settings.html` - FSN Visualization settings section
4. `cybersec/bootstrap/config.py` - FSN settings fields + TOML serialization

## Future Enhancements

1. **Partition Drill-Down**: Click table block to see partition-level detail
2. **Animation Effects**: Pulsing blocks for active recommendations
3. **What-If Preview**: Visual before/after comparison in 3D
4. **Auto-Refresh**: Poll for changes while monitoring
