"""Flink health checks.

Checks for:
- FLINK_001: TaskManager missing
- FLINK_002: Job failed
- FLINK_003: Checkpoint stale
"""

import time
from typing import Any

import httpx

from ..models import CheckResult, HealthContext
from ..catalog import FLINK_001, FLINK_002, FLINK_003


async def check_taskmanagers(ctx: HealthContext) -> CheckResult:
    """FLINK_001: Check Flink TaskManager availability.

    Verifies at least one TaskManager is registered.
    """
    if not ctx.devenv_running:
        return CheckResult.skipped("devenv not running")

    start = time.monotonic()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{ctx.flink_url}/taskmanagers", timeout=5.0)

            if resp.status_code != 200:
                duration = int((time.monotonic() - start) * 1000)
                rpn = FLINK_001.calculate_rpn()
                return CheckResult.critical(
                    f"Flink API error: {resp.status_code}",
                    failure_mode_id="FLINK_001",
                    rpn=rpn,
                    remediation="Check Flink JobManager is running",
                    status_code=resp.status_code,
                    duration_ms=duration,
                )

            data = resp.json()
            taskmanagers = data.get('taskmanagers', [])
            duration = int((time.monotonic() - start) * 1000)

            if not taskmanagers:
                rpn = FLINK_001.calculate_rpn()
                return CheckResult.critical(
                    "No TaskManagers registered",
                    failure_mode_id="FLINK_001",
                    rpn=rpn,
                    remediation="Start TaskManager: $FLINK_HOME/bin/taskmanager.sh start",
                    taskmanagers=0,
                    duration_ms=duration,
                )

            result = CheckResult.ok(
                f"TaskManagers OK: {len(taskmanagers)}",
                taskmanagers=len(taskmanagers),
            )
            result.duration_ms = duration
            return result

    except httpx.ConnectError:
        duration = int((time.monotonic() - start) * 1000)
        rpn = FLINK_001.calculate_rpn()

        # Gather diagnostic info
        diagnostics = {}
        import subprocess
        import os

        # Check FLINK_HOME
        flink_home = os.environ.get("FLINK_HOME", "")
        diagnostics["flink_home"] = flink_home or "not set"

        # Check if Flink binary exists
        if flink_home:
            flink_bin = os.path.join(flink_home, "bin", "flink")
            diagnostics["flink_built"] = os.path.exists(flink_bin)
        else:
            diagnostics["flink_built"] = False

        # Check for Flink processes
        try:
            ps_result = subprocess.run(
                ["pgrep", "-f", "org.apache.flink"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            diagnostics["flink_processes"] = len(ps_result.stdout.strip().split("\n")) if ps_result.stdout.strip() else 0
        except Exception:
            diagnostics["flink_processes"] = "check_failed"

        # Check port 8081
        try:
            lsof_result = subprocess.run(
                ["lsof", "-i", ":8081", "-t"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            diagnostics["port_8081_in_use"] = bool(lsof_result.stdout.strip())
        except Exception:
            diagnostics["port_8081_in_use"] = "check_failed"

        return CheckResult.critical(
            "Cannot connect to Flink JobManager",
            failure_mode_id="FLINK_001",
            rpn=rpn,
            remediation="Run: /health fix --apply  (or: devenv tasks run restart:clean)",
            diagnostics=diagnostics,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check TaskManagers: {e}", duration_ms=duration)


async def check_jobs(ctx: HealthContext) -> CheckResult:
    """FLINK_002: Check Flink job status.

    Looks for failed jobs and reports running job count.
    """
    start = time.monotonic()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{ctx.flink_url}/jobs/overview", timeout=5.0)

            if resp.status_code != 200:
                duration = int((time.monotonic() - start) * 1000)
                return CheckResult.error(f"Flink API error: {resp.status_code}", duration_ms=duration)

            data = resp.json()
            jobs = data.get('jobs', [])
            duration = int((time.monotonic() - start) * 1000)

            running = [j for j in jobs if j.get('state') == 'RUNNING']
            failed = [j for j in jobs if j.get('state') == 'FAILED']

            if failed:
                rpn = FLINK_002.calculate_rpn()
                failed_names = [j.get('name', j.get('jid', 'unknown')) for j in failed]
                return CheckResult.critical(
                    f"{len(failed)} failed job(s): {', '.join(failed_names)}",
                    failure_mode_id="FLINK_002",
                    rpn=rpn,
                    remediation="Check job exceptions, restart failed jobs",
                    failed_jobs=failed_names,
                    running_jobs=len(running),
                    duration_ms=duration,
                )

            if not running:
                result = CheckResult.ok(
                    "No running jobs (pipeline may be idle)",
                    running_jobs=0,
                )
                result.duration_ms = duration
                return result

            result = CheckResult.ok(
                f"Jobs OK: {len(running)} running",
                running_jobs=len(running),
            )
            result.duration_ms = duration
            return result

    except httpx.ConnectError:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.skipped("Flink not reachable")
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check jobs: {e}", duration_ms=duration)


async def check_checkpoints(ctx: HealthContext) -> CheckResult:
    """FLINK_003: Check Flink checkpoint freshness.

    Note: This is a simplified check. In production, you'd query the
    Flink REST API for checkpoint stats per job.
    """
    start = time.monotonic()

    try:
        async with httpx.AsyncClient() as client:
            # First get running jobs
            resp = await client.get(f"{ctx.flink_url}/jobs/overview", timeout=5.0)

            if resp.status_code != 200:
                return CheckResult.skipped("Cannot get jobs")

            jobs = resp.json().get('jobs', [])
            running = [j for j in jobs if j.get('state') == 'RUNNING']

            if not running:
                duration = int((time.monotonic() - start) * 1000)
                result = CheckResult.skipped("No running jobs to check checkpoints")
                result.duration_ms = duration
                return result

            # Check checkpoints for first running job
            job_id = running[0].get('jid')
            if not job_id:
                return CheckResult.skipped("No job ID found")

            resp = await client.get(
                f"{ctx.flink_url}/jobs/{job_id}/checkpoints",
                timeout=5.0
            )

            if resp.status_code != 200:
                duration = int((time.monotonic() - start) * 1000)
                # Checkpoints might not be enabled
                result = CheckResult.ok("Checkpoints not configured or unavailable")
                result.duration_ms = duration
                return result

            data = resp.json()
            duration = int((time.monotonic() - start) * 1000)

            counts = data.get('counts', {})
            completed = counts.get('completed', 0)
            failed = counts.get('failed', 0)

            if failed > 0 and completed == 0:
                rpn = FLINK_003.calculate_rpn()
                return CheckResult.warning(
                    f"All checkpoints failed ({failed} failures)",
                    failure_mode_id="FLINK_003",
                    rpn=rpn,
                    remediation="Check Flink logs for checkpoint errors",
                    completed=completed,
                    failed=failed,
                    duration_ms=duration,
                )

            if completed > 0:
                result = CheckResult.ok(
                    f"Checkpoints OK: {completed} completed",
                    completed=completed,
                    failed=failed,
                )
                result.duration_ms = duration
                return result

            result = CheckResult.ok("No checkpoints yet (job may have just started)")
            result.duration_ms = duration
            return result

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check checkpoints: {e}", duration_ms=duration)


# Registry of all flink checks
CHECKS: dict[str, Any] = {
    "FLINK_001": check_taskmanagers,
    "FLINK_002": check_jobs,
    "FLINK_003": check_checkpoints,
}
