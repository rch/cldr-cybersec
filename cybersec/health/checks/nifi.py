"""NiFi health checks.

Checks for:
- NIFI_001: NiFi not installed
- NIFI_002: NiFi not running
- NIFI_003: OTLP receiver not ready
"""

import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx

from ..models import CheckResult, HealthContext
from ..catalog import NIFI_001, NIFI_002, NIFI_003


async def check_nifi_installed(ctx: HealthContext) -> CheckResult:
    """NIFI_001: Check NiFi binary is installed.

    Verifies NiFi binary exists in the expected location. Checks:
    1. Maven build output from thirdparty/nifi submodule
    2. Extracted binary in thirdparty/nifi (legacy download)
    3. NIFI_HOME environment variable
    """
    start = time.monotonic()

    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    nifi_thirdparty = Path(devenv_root) / "thirdparty" / "nifi"

    # Check Maven build output location first (preferred - built from source)
    maven_binaries = list(nifi_thirdparty.glob("nifi-assembly/target/nifi-*-bin/nifi-*/bin/nifi.sh"))

    # Also check for extracted binary (legacy download location)
    extracted_binaries = list(nifi_thirdparty.glob("nifi-*/bin/nifi.sh"))
    # Filter out Maven build paths from extracted list
    extracted_binaries = [p for p in extracted_binaries if "nifi-assembly" not in str(p)]

    # Check NIFI_HOME environment variable
    nifi_home = os.environ.get("NIFI_HOME", "")
    nifi_home_binary = Path(nifi_home) / "bin" / "nifi.sh" if nifi_home else None

    duration = int((time.monotonic() - start) * 1000)

    if maven_binaries:
        # Maven build found - this is the preferred source build
        nifi_path = maven_binaries[0].parent.parent
        nifi_version = nifi_path.name  # e.g., "nifi-2.0.0"
        result = CheckResult.ok(
            f"NiFi installed (built from source): {nifi_version}",
            version=nifi_version,
            path=str(nifi_path),
            source="maven_build",
        )
        result.duration_ms = duration
        return result
    elif extracted_binaries:
        # Extracted binary found
        nifi_version = extracted_binaries[0].parent.parent.name
        result = CheckResult.ok(
            f"NiFi installed: {nifi_version}",
            version=nifi_version,
            path=str(extracted_binaries[0].parent.parent),
            source="extracted_binary",
        )
        result.duration_ms = duration
        return result
    elif nifi_home_binary and nifi_home_binary.exists():
        result = CheckResult.ok(
            f"NiFi installed at NIFI_HOME",
            nifi_home=nifi_home,
            source="nifi_home",
        )
        result.duration_ms = duration
        return result
    else:
        rpn = NIFI_001.calculate_rpn()
        return CheckResult.critical(
            "NiFi not installed",
            failure_mode_id="NIFI_001",
            rpn=rpn,
            remediation="Run: /health fix NIFI_001 --apply (builds from thirdparty/nifi submodule)",
            searched_paths=[str(nifi_thirdparty), nifi_home or "(NIFI_HOME not set)"],
            duration_ms=duration,
        )


async def check_nifi_running(ctx: HealthContext) -> CheckResult:
    """NIFI_002: Check NiFi web API is responding.

    Queries the NiFi system diagnostics endpoint.
    """
    if not ctx.devenv_running:
        return CheckResult.skipped("devenv not running")

    start = time.monotonic()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ctx.nifi_url}/nifi-api/system-diagnostics",
                timeout=5.0
            )

            duration = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                heap_used = (
                    data.get("systemDiagnostics", {})
                    .get("aggregateSnapshot", {})
                    .get("usedHeap", "unknown")
                )
                result = CheckResult.ok(
                    f"NiFi OK (heap: {heap_used})",
                    heap_used=heap_used,
                )
                result.duration_ms = duration
                return result
            else:
                rpn = NIFI_002.calculate_rpn()
                return CheckResult.warning(
                    f"NiFi degraded: HTTP {resp.status_code}",
                    failure_mode_id="NIFI_002",
                    rpn=rpn,
                    remediation="Restart NiFi: devenv up nifi",
                    status_code=resp.status_code,
                    duration_ms=duration,
                )

    except httpx.ConnectError:
        duration = int((time.monotonic() - start) * 1000)
        rpn = NIFI_002.calculate_rpn()
        return CheckResult.critical(
            "NiFi not reachable",
            failure_mode_id="NIFI_002",
            rpn=rpn,
            remediation="Start NiFi: devenv up nifi",
            url=ctx.nifi_url,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check NiFi: {e}", duration_ms=duration)


async def check_nifi_otlp(ctx: HealthContext) -> CheckResult:
    """NIFI_003: Check OTLP receiver port is accepting connections.

    Verifies the NiFi OTLP receiver is listening on the expected port.
    """
    if not ctx.devenv_running:
        return CheckResult.skipped("devenv not running")

    start = time.monotonic()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        result = sock.connect_ex(('localhost', ctx.nifi_otlp_port))
        sock.close()

        duration = int((time.monotonic() - start) * 1000)

        if result == 0:
            result_obj = CheckResult.ok(
                f"NiFi OTLP receiver OK (port {ctx.nifi_otlp_port})"
            )
            result_obj.duration_ms = duration
            return result_obj
        else:
            rpn = NIFI_003.calculate_rpn()
            return CheckResult.warning(
                f"NiFi OTLP receiver not responding on port {ctx.nifi_otlp_port}",
                failure_mode_id="NIFI_003",
                rpn=rpn,
                remediation="Check NiFi OTLP processor is running",
                port=ctx.nifi_otlp_port,
                duration_ms=duration,
            )

    except socket.timeout:
        duration = int((time.monotonic() - start) * 1000)
        rpn = NIFI_003.calculate_rpn()
        return CheckResult.warning(
            "NiFi OTLP receiver connection timeout",
            failure_mode_id="NIFI_003",
            rpn=rpn,
            remediation="Check NiFi flow configuration",
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check NiFi OTLP: {e}", duration_ms=duration)


# Registry of all nifi checks
CHECKS: dict[str, Any] = {
    "NIFI_001": check_nifi_installed,
    "NIFI_002": check_nifi_running,
    "NIFI_003": check_nifi_otlp,
}
