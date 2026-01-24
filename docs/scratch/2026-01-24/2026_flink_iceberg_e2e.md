# Flink + Iceberg E2E Pipeline - Complete

**Date:** 2026-01-24
**Session:** Pair programming from a driverless car on an iPad over zero-trust network to a remote tinybox

## Summary

Successfully built and validated a complete E2E data pipeline:

```
Flink DataGen → CloudTrail Events → Iceberg Tables → MinIO (S3) → Polaris REST Catalog
```

The `devenv tasks run restart:clean` now passes in ~50 seconds, verifying the entire stack from scratch.

## Commits

```
f19a6c00 Fix E2E pipeline for restart:clean validation
5873737f Fix bootstrap service JAR detection and cleanup imports
a0b325c9 Fix Flink+Iceberg integration with proper JAR configuration
154a1d43 Build Iceberg from source to fix classloader conflicts
```

## Key Fixes

### 1. Classloader Conflicts (Dropwizard Metrics)
- Built Iceberg from source (`thirdparty/iceberg`) instead of using Maven artifacts
- Added `parent-first-patterns` in Flink config for metrics packages

### 2. JAR Configuration
Required JARs in `$FLINK_HOME/lib/`:
| JAR | Purpose | Source |
|-----|---------|--------|
| `iceberg-flink-runtime-1.20-*.jar` | Flink-Iceberg connector | Built from source |
| `iceberg-aws-bundle-*.jar` | S3FileIO implementation | Built from source |
| `flink-s3-fs-hadoop-1.20.1.jar` | Hadoop + S3A classes | Flink opt/ |
| `hadoop-hdfs-client-3.4.1.jar` | HdfsConfiguration | Gradle cache |

### 3. S3 Region Configuration
AWS SDK requires explicit region even for MinIO:
- `s3.region = us-east-1`
- `client.region = us-east-1`

### 4. Checkpoint Storage
Iceberg buffers data files that exceed 5MB memory limit:
```python
t_env.get_config().set("state.checkpoint-storage", "filesystem")
t_env.get_config().set("state.checkpoints.dir", f"file://{flink_home}/checkpoints")
```

### 5. Bootstrap Script Fixes
- Use `jq` for JSON parsing instead of fragile grep
- Add retry loop for PyFlink job startup (~10-15s initialization)

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Flink DataGen  │────▶│  Iceberg Sink    │────▶│  MinIO (S3)     │
│  (PyFlink UDF)  │     │  (Checkpoints)   │     │  Parquet files  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                │
                                ▼
                        ┌──────────────────┐
                        │  Polaris REST    │
                        │  (Catalog)       │
                        └──────────────────┘
                                │
                                ▼
                        ┌──────────────────┐
                        │  Iceberg Browser │
                        │  (PyIceberg)     │
                        └─────────────────┘
```

## Validation

```bash
# Full E2E test from scratch
devenv tasks run restart:clean

# Manual verification
curl -s "http://localhost:5050/api/events?limit=1" | jq '.total'
curl -s http://localhost:8081/jobs | jq '.jobs[].status'
```

---

*This work was completed collaboratively with Claude Code, pair programming from a driverless car on an iPad, connected via zero-trust network to a remote tinybox server. The future is here.*
