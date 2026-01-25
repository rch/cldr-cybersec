"""Health check execution engine.

Orchestrates running health checks across categories and
generates HealthReport with FMEA-based prioritization.
"""

import asyncio
from typing import Optional

from .models import CheckResult, HealthContext, HealthReport
from .catalog import CATEGORIES, QUICK_CHECKS, get_failure_mode
from .checks import iceberg, flink, infra


# Check function registry - maps failure_mode_id to check function
CHECK_REGISTRY = {
    **iceberg.CHECKS,
    **flink.CHECKS,
    **infra.CHECKS,
}


class HealthRunner:
    """Execute health checks and generate reports."""

    def __init__(self):
        self.check_registry = CHECK_REGISTRY

    async def run_all(self, ctx: HealthContext) -> HealthReport:
        """Run all health checks.

        Returns a comprehensive health report with FMEA prioritization.
        """
        results: dict[str, list[CheckResult]] = {}

        for category in CATEGORIES.keys():
            category_results = await self.run_category(category, ctx)
            results[category] = category_results

        return HealthReport.from_results(results)

    async def run_category(
        self,
        category: str,
        ctx: HealthContext
    ) -> list[CheckResult]:
        """Run all checks for a specific category."""
        check_ids = CATEGORIES.get(category, [])
        results = []

        for check_id in check_ids:
            check_fn = self.check_registry.get(check_id)
            if check_fn:
                try:
                    result = await check_fn(ctx)
                    results.append(result)
                except Exception as e:
                    results.append(CheckResult.error(
                        f"Check {check_id} failed: {e}",
                        check_id=check_id,
                    ))

        return results

    async def run_quick(self, ctx: HealthContext) -> HealthReport:
        """Run only critical quick checks.

        Fast health assessment for startup or periodic monitoring.
        """
        results: dict[str, list[CheckResult]] = {
            "quick": []
        }

        # Run quick checks in parallel
        tasks = []
        for check_id in QUICK_CHECKS:
            check_fn = self.check_registry.get(check_id)
            if check_fn:
                tasks.append(self._run_check(check_id, check_fn, ctx))

        check_results = await asyncio.gather(*tasks, return_exceptions=True)

        for check_id, result in zip(QUICK_CHECKS, check_results):
            if isinstance(result, Exception):
                results["quick"].append(CheckResult.error(
                    f"Check {check_id} failed: {result}",
                    check_id=check_id,
                ))
            else:
                results["quick"].append(result)

        return HealthReport.from_results(results)

    async def _run_check(
        self,
        check_id: str,
        check_fn,
        ctx: HealthContext
    ) -> CheckResult:
        """Run a single check with error handling."""
        try:
            return await check_fn(ctx)
        except Exception as e:
            return CheckResult.error(f"Check {check_id} failed: {e}")

    async def run_single(
        self,
        failure_mode_id: str,
        ctx: HealthContext
    ) -> CheckResult:
        """Run a single check by failure mode ID."""
        check_fn = self.check_registry.get(failure_mode_id)
        if not check_fn:
            return CheckResult.skipped(f"No check registered for {failure_mode_id}")

        try:
            return await check_fn(ctx)
        except Exception as e:
            return CheckResult.error(f"Check failed: {e}")

    async def diagnose(
        self,
        failure_mode_id: str,
        ctx: HealthContext
    ) -> dict:
        """Get detailed diagnosis for a failure mode.

        Returns the failure mode definition, check result, and remediation.
        """
        failure_mode = get_failure_mode(failure_mode_id)
        if not failure_mode:
            return {"error": f"Unknown failure mode: {failure_mode_id}"}

        # Run the check
        check_result = await self.run_single(failure_mode_id, ctx)

        return {
            "failure_mode": failure_mode.to_dict(),
            "check_result": check_result.to_dict(),
            "rpn": failure_mode.calculate_rpn().to_dict(),
            "remediation_steps": failure_mode.remediation_steps,
            "symptom": failure_mode.symptom,
            "cause": failure_mode.cause,
        }


# Singleton runner instance
_runner: Optional[HealthRunner] = None


def get_runner() -> HealthRunner:
    """Get the global HealthRunner instance."""
    global _runner
    if _runner is None:
        _runner = HealthRunner()
    return _runner


async def run_health_check(
    ctx: HealthContext,
    category: Optional[str] = None,
    quick: bool = False,
) -> HealthReport:
    """Convenience function to run health checks.

    Args:
        ctx: Health context with configuration
        category: Optional category to check (iceberg, flink, infra, data)
        quick: If True, run only critical checks

    Returns:
        HealthReport with check results and FMEA prioritization
    """
    runner = get_runner()

    if quick:
        return await runner.run_quick(ctx)
    elif category:
        results = await runner.run_category(category, ctx)
        return HealthReport.from_results({category: results})
    else:
        return await runner.run_all(ctx)
