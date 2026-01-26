"""
Health check rules using RETE engine.

This module provides dependency-aware health check execution using the RETE
engine. Instead of running all checks blindly, it:

1. Models check dependencies as rules
2. Skips downstream checks when upstream services are unhealthy
3. Prioritizes remediation based on RPN scoring
4. Provides intelligent fix ordering

Key patterns:
- Infrastructure checks run first (PostgreSQL, MinIO, Polaris)
- Catalog checks depend on infrastructure being healthy
- PyFlink checks depend on Flink cluster being available
- Fixes are prioritized by RPN tier (lower RPN = auto-fix first)
"""

from dataclasses import dataclass, field
from typing import Any

from .engine import ReteEngine, Activation
from .facts import Fact, FactType, service_fact, issue_fact, check_fact
from .rules import Rule, Condition, Action, when, then


# --- Dependency Graph ---
# Level 0: Infrastructure (no dependencies)
# Level 1: Catalog/Cluster (requires infrastructure)
# Level 2: Data/Jobs (requires catalog and cluster)

CHECK_DEPENDENCIES = {
    # Level 0 - Infrastructure
    "INFRA_001": [],  # PostgreSQL
    "INFRA_002": [],  # MinIO
    "INFRA_003": [],  # Polaris
    # Level 1 - Catalog & Cluster
    "ICE_001": ["INFRA_001", "INFRA_003"],  # Iceberg catalog connection
    "ICE_002": ["ICE_001"],  # Catalog tables exist
    "FLINK_001": ["INFRA_001"],  # Flink cluster running
    "FLINK_002": ["FLINK_001"],  # TaskManagers available
    # Level 2 - PyFlink & Jobs
    "PYFLINK_001": ["FLINK_001"],  # PyFlink installed
    "PYFLINK_002": ["FLINK_001"],  # Python path correct
    "PYFLINK_011": ["FLINK_001"],  # Iceberg AWS bundle
    "PYFLINK_012": ["FLINK_001"],  # Iceberg Flink runtime
    "PYFLINK_013": [],  # Git submodules (no deps)
    # Level 3 - Data checks
    "DATA_001": ["ICE_002", "FLINK_001"],  # Snapshot accumulation
    "ICE_003": ["ICE_002"],  # Orphan files
}


# --- Health Rules ---

HEALTH_RULES = [
    # === Dependency Rules (highest priority) ===
    Rule(
        "skip_catalog_checks_postgres_down",
        conditions=when(
            ("service.postgres.healthy", "==", False),
        ),
        actions=then(
            Action.skip("ICE_001"),
            Action.skip("ICE_002"),
            Action.skip("ICE_003"),
            Action.skip("DATA_001"),
        ),
        priority=1000,
        category="dependency",
        description="Skip Iceberg catalog checks when PostgreSQL is unavailable",
    ),
    Rule(
        "skip_catalog_checks_polaris_down",
        conditions=when(
            ("service.polaris.healthy", "==", False),
        ),
        actions=then(
            Action.skip("ICE_001"),
            Action.skip("ICE_002"),
        ),
        priority=1000,
        category="dependency",
        description="Skip catalog checks when Polaris REST API is unavailable",
    ),
    Rule(
        "skip_flink_checks_cluster_down",
        conditions=when(
            ("service.flink.healthy", "==", False),
        ),
        actions=then(
            Action.skip("FLINK_002"),
            Action.skip("PYFLINK_001"),
            Action.skip("PYFLINK_002"),
            Action.skip("PYFLINK_011"),
            Action.skip("PYFLINK_012"),
            Action.skip("DATA_001"),
        ),
        priority=1000,
        category="dependency",
        description="Skip Flink/PyFlink checks when cluster is unavailable",
    ),
    Rule(
        "skip_data_checks_catalog_missing",
        conditions=when(
            ("check.ICE_002.is_critical", "==", True),
        ),
        actions=then(
            Action.skip("DATA_001"),
            Action.skip("ICE_003"),
        ),
        priority=950,
        category="dependency",
        description="Skip data checks when Iceberg tables don't exist",
    ),
    # === Auto-Remediation Rules (Tier 0: RPN <= 100) ===
    Rule(
        "auto_fix_tier0",
        conditions=when(
            ("issue.*.rpn", "<=", 100),
            ("issue.*.auto_fixable", "==", True),
        ),
        actions=then(Action.fix("{issue}")),
        priority=800,
        category="remediation",
        description="Automatically apply fixes for low-risk issues (RPN <= 100)",
    ),
    # === Guided Remediation Rules (Tier 1: 100 < RPN <= 200) ===
    Rule(
        "guided_fix_tier1",
        conditions=when(
            ("issue.*.rpn", ">", 100),
            ("issue.*.rpn", "<=", 200),
            ("issue.*.auto_fixable", "==", True),
        ),
        actions=then(
            Action.alert("Issue {issue} (RPN={rpn}) can be auto-fixed with validation", severity="info"),
            Action.fix("{issue}"),
        ),
        priority=600,
        category="remediation",
        description="Apply fixes with validation for moderate-risk issues (100 < RPN <= 200)",
    ),
    # === User Approval Rules (Tier 2: 200 < RPN <= 400) ===
    Rule(
        "prompt_fix_tier2",
        conditions=when(
            ("issue.*.rpn", ">", 200),
            ("issue.*.rpn", "<=", 400),
        ),
        actions=then(
            Action.alert("Issue {issue} (RPN={rpn}) requires user approval before fixing", severity="warning"),
        ),
        priority=400,
        category="remediation",
        description="Prompt user for approval on higher-risk issues (200 < RPN <= 400)",
    ),
    # === Manual Intervention Rules (Tier 3: RPN > 400) ===
    Rule(
        "manual_fix_tier3",
        conditions=when(
            ("issue.*.rpn", ">", 400),
        ),
        actions=then(
            Action.alert("Issue {issue} (RPN={rpn}) requires manual intervention", severity="critical"),
        ),
        priority=200,
        category="remediation",
        description="Flag high-risk issues for manual review (RPN > 400)",
    ),
    # === Specific Issue Rules ===
    Rule(
        "fix_submodules_first",
        conditions=when(
            ("issue.PYFLINK_013.rpn", ">", 0),  # Submodule issue exists
        ),
        actions=then(
            Action.fix("PYFLINK_013"),
            Action.skip("PYFLINK_011"),  # Can't build JARs without submodules
            Action.skip("PYFLINK_012"),
        ),
        priority=950,
        category="prerequisite",
        description="Initialize git submodules before attempting JAR builds",
    ),
    Rule(
        "fix_iceberg_jars_after_submodules",
        conditions=when(
            ("issue.PYFLINK_011.rpn", ">", 0),
            ("check.PYFLINK_013.is_ok", "==", True),  # Submodules initialized
        ),
        actions=then(
            Action.fix("PYFLINK_011"),
            Action.fix("PYFLINK_012"),  # Build both at once
        ),
        priority=900,
        category="prerequisite",
        description="Build Iceberg JARs after submodules are initialized",
    ),
]


@dataclass
class HealthCheckPlan:
    """Plan for executing health checks with dependencies."""

    checks_to_run: list[str]
    checks_to_skip: list[str]
    skip_reasons: dict[str, str]  # check_id -> reason
    fixes_to_apply: list[str]  # In priority order
    alerts: list[dict[str, Any]]
    execution_order: list[list[str]]  # Levels of parallel execution


class HealthRuleEngine:
    """
    RETE-based health check engine with dependency management.

    Example:
        engine = HealthRuleEngine()

        # Add service health facts
        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=False)

        # Add detected issues
        engine.add_issue("FLINK_001", rpn=280, auto_fixable=True)

        # Get execution plan
        plan = engine.plan_checks()
        print(f"Run: {plan.checks_to_run}")
        print(f"Skip: {plan.checks_to_skip}")
        print(f"Fixes: {plan.fixes_to_apply}")
    """

    def __init__(self, custom_rules: list[Rule] | None = None):
        self.engine = ReteEngine()
        self._services: dict[str, dict[str, Any]] = {}
        self._issues: dict[str, dict[str, Any]] = {}
        self._check_results: dict[str, str] = {}  # check_id -> status

        # Add default rules
        for rule in HEALTH_RULES:
            self.engine.add_rule(rule)

        # Add custom rules
        if custom_rules:
            for rule in custom_rules:
                self.engine.add_rule(rule)

    def set_service_health(
        self,
        service_id: str,
        *,
        healthy: bool,
        port: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        """Set health status for a service."""
        self._services[service_id] = {
            "healthy": healthy,
            "port": port,
            "latency_ms": latency_ms,
            "error": error,
        }
        fact = service_fact(service_id, healthy=healthy, port=port, latency_ms=latency_ms, error=error)
        self.engine.assert_fact(fact)

    def add_issue(
        self,
        failure_mode_id: str,
        *,
        rpn: int,
        severity: int = 5,
        occurrence: int = 5,
        detection: int = 5,
        auto_fixable: bool = False,
        tier: int = 2,
        category: str = "",
    ) -> None:
        """Add a detected issue."""
        self._issues[failure_mode_id] = {
            "rpn": rpn,
            "severity": severity,
            "occurrence": occurrence,
            "detection": detection,
            "auto_fixable": auto_fixable,
            "tier": tier,
            "category": category,
        }
        fact = issue_fact(
            failure_mode_id,
            rpn=rpn,
            severity=severity,
            occurrence=occurrence,
            detection=detection,
            auto_fixable=auto_fixable,
            tier=tier,
            category=category,
        )
        self.engine.assert_fact(fact)

    def set_check_result(self, check_id: str, status: str) -> None:
        """Set the result of a health check."""
        self._check_results[check_id] = status
        depends_on = CHECK_DEPENDENCIES.get(check_id, [])
        fact = check_fact(check_id, status=status, depends_on=depends_on, ran=True)
        self.engine.assert_fact(fact)

    def plan_checks(self) -> HealthCheckPlan:
        """
        Generate execution plan based on current state.

        Returns a plan with:
        - checks_to_run: Checks that should be executed
        - checks_to_skip: Checks that should be skipped (with reasons)
        - fixes_to_apply: Fixes to apply in priority order
        - alerts: Alerts to display
        - execution_order: Checks grouped by dependency level
        """
        result = self.engine.solve()

        checks_to_skip: dict[str, str] = {}
        fixes_to_apply: list[tuple[int, str]] = []  # (priority, fix_id)
        alerts: list[dict[str, Any]] = []

        if result.success:
            for activation in result.activations:
                for action in activation.actions:
                    if action.action_type == "skip":
                        checks_to_skip[action.target] = activation.rule.description
                    elif action.action_type == "fix":
                        fixes_to_apply.append((activation.priority, action.target))
                    elif action.action_type == "alert":
                        alerts.append(
                            {
                                "message": action.target,
                                "severity": action.params.get("severity", "info"),
                                "rule_id": activation.rule_id,
                            }
                        )

        # Sort fixes by priority (highest first)
        fixes_to_apply.sort(reverse=True)
        ordered_fixes = [fix_id for _, fix_id in fixes_to_apply]

        # Build execution order by dependency level
        all_checks = set(CHECK_DEPENDENCIES.keys())
        checks_to_run = all_checks - set(checks_to_skip.keys())
        execution_order = self._topological_order(checks_to_run)

        return HealthCheckPlan(
            checks_to_run=sorted(checks_to_run),
            checks_to_skip=sorted(checks_to_skip.keys()),
            skip_reasons=checks_to_skip,
            fixes_to_apply=ordered_fixes,
            alerts=alerts,
            execution_order=execution_order,
        )

    def _topological_order(self, checks: set[str]) -> list[list[str]]:
        """Order checks by dependency level for parallel execution."""
        levels: list[list[str]] = []
        remaining = checks.copy()
        satisfied: set[str] = set()

        while remaining:
            # Find checks whose dependencies are all satisfied
            level = []
            for check_id in list(remaining):
                deps = set(CHECK_DEPENDENCIES.get(check_id, []))
                # Check is ready if all deps are satisfied OR skipped
                if deps <= satisfied or not deps:
                    level.append(check_id)
                    remaining.remove(check_id)

            if not level:
                # Circular dependency or missing deps - add remaining as final level
                levels.append(sorted(remaining))
                break

            levels.append(sorted(level))
            satisfied.update(level)

        return levels

    def explain_skip(self, check_id: str) -> dict[str, Any]:
        """Explain why a check was/would be skipped."""
        # Find rules that might skip this check
        for rule in HEALTH_RULES:
            for action in rule.actions:
                if action.action_type == "skip" and action.target == check_id:
                    explanation = self.engine.explain(rule.rule_id)
                    if explanation.get("fired"):
                        return {
                            "check_id": check_id,
                            "skipped": True,
                            "reason": rule.description,
                            "rule_id": rule.rule_id,
                            "conditions": explanation.get("conditions", []),
                        }

        return {
            "check_id": check_id,
            "skipped": False,
            "reason": "Check will run normally",
        }

    def get_fix_order(self) -> list[tuple[str, int, str]]:
        """
        Get recommended fix order with priorities.

        Returns list of (failure_mode_id, priority, description).
        """
        plan = self.plan_checks()
        result = []

        for fix_id in plan.fixes_to_apply:
            issue = self._issues.get(fix_id, {})
            rpn = issue.get("rpn", 0)

            # Find the rule that triggered this fix
            desc = f"Fix {fix_id}"
            for rule in HEALTH_RULES:
                for action in rule.actions:
                    if action.action_type == "fix" and "{issue}" in action.target:
                        desc = rule.description
                        break

            result.append((fix_id, rpn, desc))

        return result
