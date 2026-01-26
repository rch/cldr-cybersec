"""Infrastructure health checks.

Checks for:
- INFRA_001: PostgreSQL down
- INFRA_002: MinIO unhealthy
- INFRA_003: Polaris degraded
- INFRA_004: macOS Shared Memory Exhaustion
"""

import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx

from ..models import CheckResult, HealthContext
from ..catalog import INFRA_001, INFRA_002, INFRA_003, INFRA_004


async def check_postgres(ctx: HealthContext) -> CheckResult:
    """INFRA_001: Check PostgreSQL connectivity.

    Simple TCP connection test to the database port.
    """
    start = time.monotonic()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        result = sock.connect_ex(('localhost', ctx.postgres_port))
        sock.close()

        duration = int((time.monotonic() - start) * 1000)

        if result == 0:
            result_obj = CheckResult.ok(f"PostgreSQL OK (port {ctx.postgres_port})")
            result_obj.duration_ms = duration
            return result_obj
        else:
            rpn = INFRA_001.calculate_rpn()
            return CheckResult.critical(
                f"PostgreSQL not reachable on port {ctx.postgres_port}",
                failure_mode_id="INFRA_001",
                rpn=rpn,
                remediation="Start PostgreSQL: devenv up postgres",
                port=ctx.postgres_port,
                duration_ms=duration,
            )

    except socket.timeout:
        duration = int((time.monotonic() - start) * 1000)
        rpn = INFRA_001.calculate_rpn()
        return CheckResult.critical(
            "PostgreSQL connection timeout",
            failure_mode_id="INFRA_001",
            rpn=rpn,
            remediation="Check PostgreSQL is running",
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check PostgreSQL: {e}", duration_ms=duration)


async def check_minio(ctx: HealthContext) -> CheckResult:
    """INFRA_002: Check MinIO health.

    Queries the MinIO health endpoint.
    """
    start = time.monotonic()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ctx.minio_endpoint}/minio/health/live",
                timeout=5.0
            )

            duration = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                result = CheckResult.ok("MinIO OK")
                result.duration_ms = duration
                return result
            else:
                rpn = INFRA_002.calculate_rpn()
                return CheckResult.critical(
                    f"MinIO unhealthy: {resp.status_code}",
                    failure_mode_id="INFRA_002",
                    rpn=rpn,
                    remediation="Check MinIO: devenv up minio",
                    status_code=resp.status_code,
                    duration_ms=duration,
                )

    except httpx.ConnectError:
        duration = int((time.monotonic() - start) * 1000)
        rpn = INFRA_002.calculate_rpn()
        return CheckResult.critical(
            "MinIO not reachable",
            failure_mode_id="INFRA_002",
            rpn=rpn,
            remediation="Start MinIO: devenv up minio",
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check MinIO: {e}", duration_ms=duration)


async def check_polaris(ctx: HealthContext) -> CheckResult:
    """INFRA_003: Check Polaris health.

    Queries the Polaris admin health endpoint and measures latency.
    """
    start = time.monotonic()

    # Polaris admin port is typically 8182
    admin_url = ctx.polaris_url.replace(":8181", ":8182")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{admin_url}/q/health/ready",
                timeout=5.0
            )

            duration = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                # Check for slow response
                if duration > 2000:  # 2 seconds is slow
                    rpn = INFRA_003.calculate_rpn()
                    return CheckResult.warning(
                        f"Polaris slow: {duration}ms response",
                        failure_mode_id="INFRA_003",
                        rpn=rpn,
                        remediation="Restart Polaris or check resources",
                        response_ms=duration,
                        duration_ms=duration,
                    )

                result = CheckResult.ok(f"Polaris OK ({duration}ms)")
                result.duration_ms = duration
                return result
            else:
                rpn = INFRA_003.calculate_rpn()
                return CheckResult.warning(
                    f"Polaris degraded: {resp.status_code}",
                    failure_mode_id="INFRA_003",
                    rpn=rpn,
                    remediation="Restart Polaris: devenv tasks run restart:polaris",
                    status_code=resp.status_code,
                    duration_ms=duration,
                )

    except httpx.ConnectError:
        duration = int((time.monotonic() - start) * 1000)
        rpn = INFRA_003.calculate_rpn()
        return CheckResult.critical(
            "Polaris not reachable",
            failure_mode_id="INFRA_003",
            rpn=rpn,
            remediation="Start Polaris: devenv up polaris",
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check Polaris: {e}", duration_ms=duration)


async def check_shared_memory(ctx: HealthContext) -> CheckResult:
    """INFRA_004: Check for orphaned shared memory segments.

    On macOS, orphaned IPC shared memory segments from previous devenv crashes
    can accumulate and exhaust system limits, preventing PostgreSQL from starting.

    Proactively checks for orphaned segments owned by current user via ipcs -m.
    """
    import platform
    import subprocess

    start = time.monotonic()

    # Only relevant on macOS (Linux handles this differently)
    if platform.system() != "Darwin":
        return CheckResult.skipped("Shared memory check only applies to macOS")

    try:
        # Check for orphaned shared memory segments
        result = subprocess.run(
            ["ipcs", "-m"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            duration = int((time.monotonic() - start) * 1000)
            return CheckResult.error(f"ipcs command failed: {result.stderr}", duration_ms=duration)

        # Parse ipcs output - count segments owned by current user
        # Format: T ID KEY MODE OWNER GROUP (header + data lines)
        lines = result.stdout.strip().split('\n')
        current_user = os.environ.get("USER", "")

        orphan_count = 0
        for line in lines:
            parts = line.split()
            # Skip header lines and empty lines
            if len(parts) >= 5 and parts[0] == 'm':
                owner = parts[4] if len(parts) > 4 else ""
                if owner == current_user:
                    orphan_count += 1

        duration = int((time.monotonic() - start) * 1000)

        # Any segments from current user are likely orphaned (PostgreSQL cleans up on normal exit)
        if orphan_count > 0:
            rpn = INFRA_004.calculate_rpn()
            return CheckResult.warning(
                f"Found {orphan_count} orphaned shared memory segment(s)",
                failure_mode_id="INFRA_004",
                rpn=rpn,
                remediation="Run: /health fix --apply",
                segment_count=orphan_count,
                duration_ms=duration,
            )

        result_obj = CheckResult.ok("No orphaned shared memory segments")
        result_obj.duration_ms = duration
        return result_obj

    except subprocess.TimeoutExpired:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error("ipcs command timed out", duration_ms=duration)
    except FileNotFoundError:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.skipped("ipcs command not found")
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check shared memory: {e}", duration_ms=duration)


# Registry of all infra checks
CHECKS: dict[str, Any] = {
    "INFRA_001": check_postgres,
    "INFRA_002": check_minio,
    "INFRA_003": check_polaris,
    "INFRA_004": check_shared_memory,
}
