# Bootstrap System

The bootstrap system provides unified configuration and setup across CLI, Web UI, and MCP interfaces.

## First-Time Setup

On `devenv up`, the bootstrap-check process runs automatically and displays environment status. If bootstrap is needed:

1. **Web UI**: Visit http://localhost:5050/settings and click "Run Bootstrap"
2. **CLI**: Run `cybersec bootstrap run`
3. **MCP**: Use the `bootstrap_run` tool from Claude Code

## Commands

```bash
# Check current configuration
cybersec bootstrap info

# Check service health
cybersec bootstrap status

# View/modify settings
cybersec bootstrap settings --show
cybersec bootstrap settings --edit
cybersec bootstrap settings --set flink_home=/path/to/flink

# Run verification checks
cybersec bootstrap verify

# Run bootstrap process
cybersec bootstrap run
cybersec bootstrap run --flink-path ~/local/flink-1.20.1
cybersec bootstrap run --skip-flink
cybersec bootstrap run --dry-run

# Quick assessment (for automation)
cybersec bootstrap assess
```

## Configuration

Configuration is stored in `.cybersec/config.toml`:

```toml
[bootstrap]
completed = true
last_run = "2025-01-23T12:00:00"

[paths]
flink_home = ""  # Empty = build from thirdparty/flink
minio_data_dir = ""  # Empty = $DEVENV_STATE/minio

[services.postgres]
host = "localhost"
port = 5438

[services.polaris]
api_url = "http://localhost:8181"
admin_url = "http://localhost:8182"

[catalog]
name = "cybersec"
warehouse = "s3://cybersec/iceberg/warehouse"
```

## MCP Tools

The MCP server provides these tools for AI agents:

| Tool | Description |
|------|-------------|
| `bootstrap_info` | Get configuration and status |
| `bootstrap_status` | Check service health |
| `bootstrap_settings` | View/update settings |
| `bootstrap_verify` | Run verification checks |
| `bootstrap_run` | Execute bootstrap process |
| `bootstrap_assess` | Quick assessment |

## Web UI Integration

Bootstrap routes are integrated into the Iceberg Browser:

- `/settings` - Bootstrap settings page
- `/api/bootstrap/info` - Configuration API
- `/api/bootstrap/status` - Service health API
- `/api/bootstrap/settings` - Settings GET/POST API
- `/api/bootstrap/verify` - Verification API
- `/api/bootstrap/run` - Bootstrap execution (SSE stream)

## Bootstrap Process

When you run `cybersec bootstrap run`:

1. **Check prerequisites**: Python, Git, network connectivity
2. **Verify services**: PostgreSQL, MinIO, Polaris
3. **Build Flink** (if needed): Compile from `thirdparty/flink/`
4. **Initialize catalog**: Create 'cybersec' catalog in Polaris
5. **Verify E2E**: Run health diagnostics

## Troubleshooting

If bootstrap fails:

```bash
# Check specific service
cybersec bootstrap status

# Run verification only
cybersec bootstrap verify

# Check health diagnostics
cybersec health
```

See [Troubleshooting](../reference/troubleshooting.md) for common issues.
