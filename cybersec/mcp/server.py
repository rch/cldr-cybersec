"""MCP Server implementation using fastmcp.

Provides MCP tools with full parity to CLI and Web UI interfaces.

Tools:
- bootstrap_info: Get bootstrap configuration and status
- bootstrap_status: Check health of all services
- bootstrap_settings: View or update bootstrap settings
- bootstrap_verify: Verify environment is correctly configured
- bootstrap_run: Execute bootstrap process
- bootstrap_assess: Quick assessment for devenv startup
- bootstrap_health: FMEA-based deep health diagnostics
- bootstrap_diagnose: Detailed diagnosis for a failure mode
- bootstrap_fix: Attempt remediation based on FMEA tier

Resources:
- bootstrap://config: Current configuration
- bootstrap://state: Current bootstrap state
"""

import asyncio
from typing import Optional, Any
from pathlib import Path

from fastmcp import FastMCP

from ..bootstrap import (
    BootstrapService,
    BootstrapConfig,
    EventType,
    BootstrapEvent,
)
from ..bootstrap.events import EventCollector

# Create MCP server
mcp = FastMCP(
    name="cybersec",
    instructions="""Cybersec Toolkit MCP Server

This server provides tools for managing the cybersec devenv environment.
Use these tools to check status, configure settings, and run bootstrap.

Common workflows:
1. Check status: Use bootstrap_status to see if services are healthy
2. Get info: Use bootstrap_info to see current configuration
3. Run bootstrap: Use bootstrap_run to set up the environment
4. Verify: Use bootstrap_verify to check everything is correct
""",
)


def _get_service() -> BootstrapService:
    """Create a bootstrap service instance."""
    return BootstrapService()


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool()
async def bootstrap_info() -> dict:
    """Get bootstrap system information and current configuration.

    Returns the complete bootstrap configuration including:
    - Service endpoints (PostgreSQL, Polaris, Flink, MinIO)
    - Paths (Flink home, MinIO data, logs)
    - Catalog configuration
    - Bootstrap completion status

    Use this to understand the current environment setup.
    """
    service = _get_service()
    config = service.get_config()

    return {
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
            "postgres": {
                "host": config.postgres_host,
                "port": config.postgres_port,
                "database": config.postgres_database,
            },
            "polaris": {
                "api_url": config.polaris_api_url,
                "admin_url": config.polaris_admin_url,
            },
            "flink": {
                "url": config.flink_url,
            },
            "minio": {
                "endpoint": config.minio_endpoint,
                "console": config.minio_console,
            },
            "iceberg_browser": {
                "port": config.iceberg_browser_port,
            },
            "nifi": {
                "url": config.nifi_url,
                "otlp_port": config.nifi_otlp_port,
            },
        },
        "nifi": {
            "home": str(config.get_nifi_home()) if config.get_nifi_home() else None,
            "version": config.nifi_version,
        },
        "catalog": {
            "name": config.catalog_name,
            "warehouse": config.catalog_warehouse,
        },
    }


@mcp.tool()
async def bootstrap_status() -> dict:
    """Check current status of all services and bootstrap state.

    Performs health checks on:
    - PostgreSQL: Database connectivity
    - Polaris: REST catalog API health
    - Flink: JobManager availability
    - MinIO: Object storage health
    - Iceberg Browser: Web UI availability

    Returns health status for each service.
    Use this before running jobs to ensure the environment is ready.
    """
    service = _get_service()
    results = await service.check_all_services()

    all_healthy = all(r.get("healthy", False) for r in results)

    return {
        "all_healthy": all_healthy,
        "services": results,
        "recommendation": None if all_healthy else "Run bootstrap_run to set up the environment",
    }


@mcp.tool()
async def bootstrap_settings(
    show: bool = True,
    updates: Optional[dict] = None,
    reset: bool = False,
) -> dict:
    """View or modify bootstrap settings.

    Args:
        show: If True, return current settings (default)
        updates: Dictionary of settings to update (e.g., {"flink_home": "/path/to/flink"})
        reset: If True, reset all settings to defaults

    Available settings:
    - flink_home: Path to Flink installation
    - minio_data_dir: MinIO data directory
    - postgres_port: PostgreSQL port
    - iceberg_browser_port: Web UI port
    - catalog_name: Iceberg catalog name

    Returns the current settings after any changes.
    """
    service = _get_service()

    if reset:
        config = BootstrapConfig()
        service.settings.save(config)
        return {
            "action": "reset",
            "message": "Settings reset to defaults",
            "config": service.get_config_dict(),
        }

    if updates:
        service.update_config(**updates)
        return {
            "action": "updated",
            "message": f"Updated {len(updates)} setting(s)",
            "updated_keys": list(updates.keys()),
            "config": service.get_config_dict(),
        }

    return {
        "action": "show",
        "config": service.get_config_dict(),
    }


@mcp.tool()
async def bootstrap_verify() -> dict:
    """Verify the bootstrap configuration is correct and complete.

    Performs comprehensive checks:
    - Required tools (java, mvn, psql, curl, jq, git)
    - Flink installation
    - Service connectivity
    - Bootstrap configuration file
    - Bootstrap completion status

    Returns a detailed report of all checks.
    Use this to diagnose environment issues.
    """
    service = _get_service()
    result = await service.verify()

    return {
        "all_passed": result.get("all_passed", False),
        "checks": result.get("checks", []),
        "summary": {
            "passed": sum(1 for c in result.get("checks", []) if c["passed"]),
            "failed": sum(1 for c in result.get("checks", []) if not c["passed"]),
            "total": len(result.get("checks", [])),
        },
    }


@mcp.tool()
async def bootstrap_run(
    skip_flink: bool = False,
    flink_path: Optional[str] = None,
    skip_nifi: bool = False,
    nifi_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Execute the bootstrap process to set up the environment.

    This will:
    1. Check environment requirements (tools, ports)
    2. Create required directories
    3. Initialize git submodules
    4. Configure Flink (build from source or use existing)
    5. Configure NiFi (download binary or use existing)
    6. Save configuration

    Args:
        skip_flink: Skip Flink setup entirely
        flink_path: Path to existing Flink installation (e.g., "/home/user/flink-1.20.1")
        skip_nifi: Skip NiFi setup entirely
        nifi_path: Path to existing NiFi installation
        dry_run: If True, show what would be done without making changes

    Note: If flink_path is not provided and Flink is not found,
    the tool will indicate that user input is needed. In that case,
    call this tool again with flink_path set to either:
    - A path to an existing Flink installation, or
    - "build" to build from source (takes 10-15 minutes)
    - "skip" to skip Flink setup

    Returns progress events and final status.
    """
    service = _get_service()
    collector = EventCollector()

    # For MCP, we handle prompts by returning a special response
    prompt_needed = None

    async def mcp_prompt_handler(event: BootstrapEvent) -> Optional[str]:
        nonlocal prompt_needed
        if event.event_type == EventType.PROMPT_REQUIRED:
            # Store the prompt info - we'll return it to the LLM
            prompt_needed = {
                "message": event.message,
                "options": [
                    {"key": o.key, "label": o.label, "description": o.description, "default": o.default}
                    for o in event.prompt_options
                ],
                "allow_custom": event.prompt_allow_custom,
            }
            # Return skip for now - the LLM should call again with flink_path
            return "3"  # Skip option
        return None

    events = []
    final_status = None

    try:
        async for event in service.run(
            skip_flink=skip_flink,
            flink_path=flink_path,
            skip_nifi=skip_nifi,
            nifi_path=nifi_path,
            prompt_handler=mcp_prompt_handler,
            dry_run=dry_run,
        ):
            collector.collect(event)
            events.append({
                "type": event.event_type.value,
                "task_id": event.task_id,
                "message": event.message,
                "progress": event.progress,
            })

            if event.event_type == EventType.BOOTSTRAP_COMPLETED:
                final_status = "success"
            elif event.event_type == EventType.BOOTSTRAP_FAILED:
                final_status = "failed"

    except Exception as e:
        final_status = "error"
        events.append({
            "type": "error",
            "message": str(e),
        })

    result = {
        "status": final_status or "unknown",
        "events": events,
        "summary": collector.to_summary(),
    }

    if prompt_needed:
        result["prompt_needed"] = prompt_needed
        result["hint"] = (
            "Flink setup requires input. Call bootstrap_run again with "
            "flink_path='/path/to/flink' or flink_path='build' or skip_flink=True"
        )

    return result


@mcp.tool()
async def bootstrap_assess() -> dict:
    """Quick environment assessment (for startup checks).

    Performs a fast assessment of the environment:
    - Config file existence
    - Required tools
    - Flink installation
    - Whether bootstrap is needed

    Returns a summary suitable for automated checks.
    Use this for quick status checks without full verification.
    """
    service = _get_service()
    result = await service.assess()

    return {
        "ready": result.get("ready", False),
        "needs_bootstrap": result.get("needs_bootstrap", True),
        "config_exists": result.get("config_exists", False),
        "flink_installed": result.get("flink_installed", False),
        "flink_home": result.get("flink_home"),
        "nifi_installed": result.get("nifi_installed", False),
        "nifi_home": result.get("nifi_home"),
        "tools": result.get("tools", {}),
        "settings_url": result.get("settings_url"),
        "recommendation": (
            "Environment is ready" if result.get("ready")
            else "Run bootstrap_run to set up the environment"
        ),
    }


# ============================================================================
# Health Check Tools
# ============================================================================


@mcp.tool()
async def bootstrap_health(
    category: Optional[str] = None,
    quick: bool = False,
) -> dict:
    """Run FMEA-based health diagnostics on the cybersec environment.

    Goes beyond basic connectivity to check:
    - iceberg: PyIceberg memory usage, catalog connectivity, data freshness
    - flink: Job status, TaskManager availability, checkpoint health
    - infra: PostgreSQL, MinIO, Polaris connectivity
    - data: Snapshot accumulation, data quality

    Args:
        category: Specific category to check (iceberg, flink, infra, data)
                  or None for all checks
        quick: If True, run only critical infrastructure checks (fast)

    Returns:
        Health report with:
        - status: "healthy", "degraded", or "critical"
        - checks: Results grouped by category
        - issues: Detected issues with RPN scores
        - recommendations: Prioritized remediation steps
    """
    from ..health.models import HealthContext
    from ..health.runner import run_health_check

    service = _get_service()
    config = service.get_config()

    # Create health context
    ctx = HealthContext(
        config=config,
        flink_url=config.flink_url or "http://localhost:8081",
        minio_endpoint=config.minio_endpoint or "http://localhost:9010",
        polaris_url=config.polaris_api_url or "http://localhost:8181",
        postgres_port=config.postgres_port or 5438,
        browser_port=config.iceberg_browser_port or 5050,
    )

    # Run health checks
    report = await run_health_check(ctx, category=category, quick=quick)

    return report.to_dict()


@mcp.tool()
async def bootstrap_diagnose(failure_mode_id: str) -> dict:
    """Get detailed diagnosis for a specific failure mode.

    Returns the heuristic definition, current check status,
    RPN calculation, and remediation steps.

    Args:
        failure_mode_id: The failure mode to diagnose (e.g., "ICE_001", "FLINK_002")

    Returns:
        Detailed diagnosis including:
        - failure_mode: Definition from catalog
        - check_result: Current status from running the check
        - rpn: Risk Priority Number calculation
        - remediation_steps: How to fix the issue
    """
    from ..health.models import HealthContext
    from ..health.runner import get_runner

    service = _get_service()
    config = service.get_config()

    ctx = HealthContext(
        config=config,
        flink_url=config.flink_url or "http://localhost:8081",
        minio_endpoint=config.minio_endpoint or "http://localhost:9010",
        polaris_url=config.polaris_api_url or "http://localhost:8181",
        postgres_port=config.postgres_port or 5438,
        browser_port=config.iceberg_browser_port or 5050,
    )

    runner = get_runner()
    return await runner.diagnose(failure_mode_id, ctx)


@mcp.tool()
async def bootstrap_fix(
    failure_mode_id: str,
    dry_run: bool = True,
) -> dict:
    """Attempt remediation for a failure mode based on escalation tier.

    For TIER_0/TIER_1 issues (RPN <= 200), can auto-remediate.
    For TIER_2/MANUAL issues (RPN > 200), returns instructions only.

    Args:
        failure_mode_id: The failure mode to fix (e.g., "ICE_001")
        dry_run: If True, show what would be done without making changes

    Returns:
        Fix result including:
        - action: "executed", "dry_run", or "manual_required"
        - tier: Escalation tier from FMEA
        - instructions: Remediation steps (if manual)
        - result: Execution result (if not dry_run)
    """
    from ..health.catalog import get_failure_mode

    failure_mode = get_failure_mode(failure_mode_id)
    if not failure_mode:
        return {"error": f"Unknown failure mode: {failure_mode_id}"}

    rpn = failure_mode.calculate_rpn()
    tier = rpn.tier

    # For now, return instructions for all tiers
    # In the future, TIER_0/TIER_1 could auto-execute remediation
    return {
        "failure_mode_id": failure_mode_id,
        "tier": tier.name,
        "tier_value": int(tier),
        "requires_approval": rpn.requires_approval,
        "dry_run": dry_run,
        "action": "manual_required" if rpn.requires_approval else "dry_run" if dry_run else "would_execute",
        "instructions": failure_mode.remediation_steps,
        "symptom": failure_mode.symptom,
        "target_state": f"Resolve {failure_mode.name}",
    }


# ============================================================================
# MCP Resources
# ============================================================================


@mcp.resource("bootstrap://config")
async def get_config_resource() -> str:
    """Get current bootstrap configuration as JSON."""
    import json

    service = _get_service()
    return json.dumps(service.get_config_dict(), indent=2)


@mcp.resource("bootstrap://state")
async def get_state_resource() -> str:
    """Get current bootstrap state as JSON."""
    import json

    service = _get_service()
    return json.dumps(service.state.to_dict(), indent=2)


# ============================================================================
# Server Entry Point
# ============================================================================


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
