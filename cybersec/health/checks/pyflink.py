"""PyFlink health checks.

Checks for:
- PYFLINK_001: PyFlink not installed
- PYFLINK_011: Iceberg AWS bundle missing
- PYFLINK_012: Iceberg Flink runtime missing
- PYFLINK_013: Git submodules not initialized
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..models import CheckResult, HealthContext
from ..catalog import get_failure_mode


async def check_pyflink_installed(ctx: HealthContext) -> CheckResult:
    """PYFLINK_001: Check PyFlink is installed in devenv Python.

    Verifies the devenv Python can import pyflink module.
    """
    start = time.monotonic()

    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
    devenv_python = Path(devenv_root) / ".devenv" / "profile" / "bin" / "python3"

    if not devenv_python.exists():
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.skipped(
            "Devenv Python not found",
            devenv_python=str(devenv_python),
            duration_ms=duration,
        )

    try:
        result = subprocess.run(
            [str(devenv_python), "-c", "import pyflink; print(pyflink.__version__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        duration = int((time.monotonic() - start) * 1000)

        if result.returncode == 0:
            version = result.stdout.strip()
            result_obj = CheckResult.ok(
                f"PyFlink installed: {version}",
                version=version,
                python=str(devenv_python),
            )
            result_obj.duration_ms = duration
            return result_obj
        else:
            fm = get_failure_mode("PYFLINK_001")
            rpn = fm.calculate_rpn() if fm else None

            return CheckResult.critical(
                "PyFlink not installed in devenv Python",
                failure_mode_id="PYFLINK_001",
                rpn=rpn,
                remediation="Run: uv sync",
                python=str(devenv_python),
                error=result.stderr.strip(),
                duration_ms=duration,
            )

    except subprocess.TimeoutExpired:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error("PyFlink check timed out", duration_ms=duration)
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check PyFlink: {e}", duration_ms=duration)


async def check_submodules(ctx: HealthContext) -> CheckResult:
    """PYFLINK_013: Check git submodules are initialized.

    Verifies thirdparty/iceberg and thirdparty/flink are properly initialized.
    """
    start = time.monotonic()

    devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())

    # Check Iceberg submodule
    iceberg_gradlew = Path(devenv_root) / "thirdparty" / "iceberg" / "gradlew"
    iceberg_initialized = iceberg_gradlew.exists()

    # Check Flink submodule
    flink_pom = Path(devenv_root) / "thirdparty" / "flink" / "pom.xml"
    flink_initialized = flink_pom.exists()

    duration = int((time.monotonic() - start) * 1000)

    if not iceberg_initialized or not flink_initialized:
        fm = get_failure_mode("PYFLINK_013")
        rpn = fm.calculate_rpn() if fm else None

        missing = []
        if not iceberg_initialized:
            missing.append("thirdparty/iceberg")
        if not flink_initialized:
            missing.append("thirdparty/flink")

        return CheckResult.critical(
            f"Git submodules not initialized: {', '.join(missing)}",
            failure_mode_id="PYFLINK_013",
            rpn=rpn,
            remediation="Run: git submodule update --init --recursive",
            missing_submodules=missing,
            duration_ms=duration,
        )

    result = CheckResult.ok(
        "Git submodules initialized",
        iceberg=str(iceberg_gradlew.parent),
        flink=str(flink_pom.parent),
    )
    result.duration_ms = duration
    return result


async def check_iceberg_jars(ctx: HealthContext) -> CheckResult:
    """PYFLINK_011/012: Check Iceberg JARs are installed in Flink lib.

    Verifies iceberg-flink-runtime and iceberg-aws-bundle JARs exist.
    """
    start = time.monotonic()

    # Get Flink home
    flink_home = ctx.config.get_flink_home() if ctx.config else None
    if not flink_home:
        devenv_root = os.environ.get("DEVENV_ROOT", os.getcwd())
        flink_home = Path(devenv_root) / "thirdparty" / "flink" / "flink-dist" / "target" / "flink-1.20.1-bin" / "flink-1.20.1"

    if not flink_home.exists():
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.skipped(
            "Flink not installed - skipping JAR check",
            flink_home=str(flink_home),
            duration_ms=duration,
        )

    lib_dir = flink_home / "lib"
    if not lib_dir.exists():
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(
            f"Flink lib directory not found: {lib_dir}",
            duration_ms=duration,
        )

    # Check for JARs
    flink_runtime_jars = list(lib_dir.glob("iceberg-flink-runtime-1.20-*.jar"))
    aws_bundle_jars = list(lib_dir.glob("iceberg-aws-bundle-*.jar"))

    duration = int((time.monotonic() - start) * 1000)

    missing = []
    if not flink_runtime_jars:
        missing.append("iceberg-flink-runtime-1.20-*.jar")
    if not aws_bundle_jars:
        missing.append("iceberg-aws-bundle-*.jar")

    if missing:
        # Use PYFLINK_011 for AWS bundle (more common/critical)
        fm = get_failure_mode("PYFLINK_011")
        rpn = fm.calculate_rpn() if fm else None

        return CheckResult.critical(
            f"Missing Iceberg JARs: {', '.join(missing)}",
            failure_mode_id="PYFLINK_011",
            rpn=rpn,
            remediation="Run: /health fix --apply",
            missing_jars=missing,
            lib_dir=str(lib_dir),
            duration_ms=duration,
        )

    result = CheckResult.ok(
        f"Iceberg JARs OK: {len(flink_runtime_jars)} runtime, {len(aws_bundle_jars)} AWS bundle",
        flink_runtime=[j.name for j in flink_runtime_jars],
        aws_bundle=[j.name for j in aws_bundle_jars],
    )
    result.duration_ms = duration
    return result


# Registry of all pyflink checks
CHECKS: dict[str, Any] = {
    "PYFLINK_001": check_pyflink_installed,
    "PYFLINK_011": check_iceberg_jars,
    "PYFLINK_012": check_iceberg_jars,  # Same check covers both
    "PYFLINK_013": check_submodules,
}
