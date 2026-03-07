"""K8s health checks.

Checks for:
- K8S_001: Kubeconfig stale after RKE2 restart
"""

import time
from pathlib import Path
from typing import Any

from ..models import CheckResult, HealthContext
from ..catalog import K8S_001


async def check_kubeconfig_freshness(ctx: HealthContext) -> CheckResult:
    """K8S_001: Check if user kubeconfig is stale after RKE2 restart.

    Compares mtime of system kubeconfig (/etc/rancher/rke2/rke2.yaml)
    against user copy (~/.kube/rke2.yaml). Skips if not an RKE2 system.
    """
    from ...k8s.rke2 import check_kubeconfig_freshness as check_freshness, RKE2Config

    start = time.monotonic()

    # Get RKE2 config from bootstrap config
    try:
        rke2_config = ctx.config.get_rke2_config()
    except (AttributeError, Exception):
        rke2_config = RKE2Config()

    system_path = Path(rke2_config.system_kubeconfig)

    # Skip if not an RKE2 system
    if not system_path.exists():
        return CheckResult.skipped("Not an RKE2 system (no system kubeconfig)")

    try:
        freshness = check_freshness(rke2_config)
        duration = int((time.monotonic() - start) * 1000)

        if not freshness["user_exists"]:
            rpn = K8S_001.calculate_rpn()
            return CheckResult.critical(
                "User kubeconfig missing (system kubeconfig exists)",
                failure_mode_id="K8S_001",
                rpn=rpn,
                remediation="Run: /k8s rke2 refresh --apply",
                duration_ms=duration,
            )

        if freshness["stale"]:
            staleness = freshness.get("staleness_seconds", 0) or 0
            rpn = K8S_001.calculate_rpn()
            return CheckResult.warning(
                f"Kubeconfig stale by {staleness:.0f}s",
                failure_mode_id="K8S_001",
                rpn=rpn,
                remediation="Run: /k8s rke2 refresh --apply",
                staleness_seconds=staleness,
                duration_ms=duration,
            )

        result = CheckResult.ok("Kubeconfig up to date")
        result.duration_ms = duration
        return result

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return CheckResult.error(f"Failed to check kubeconfig: {e}", duration_ms=duration)


# Registry of all k8s checks
CHECKS: dict[str, Any] = {
    "K8S_001": check_kubeconfig_freshness,
}
