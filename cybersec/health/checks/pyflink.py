"""PyFlink health checks.

Checks for:
- PYFLINK_011: Iceberg AWS bundle missing
- PYFLINK_012: Iceberg Flink runtime missing
- PYFLINK_013: Git submodules not initialized
"""

import os
import time
from pathlib import Path
from typing import Any

from ..models import CheckResult, HealthContext
from ..catalog import get_failure_mode


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
            remediation="Run: cybersec --cmd '/health fix pyflink'",
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
    "PYFLINK_011": check_iceberg_jars,
    "PYFLINK_012": check_iceberg_jars,  # Same check covers both
    "PYFLINK_013": check_submodules,
}
