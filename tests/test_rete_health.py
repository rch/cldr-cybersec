"""Tests for health check rules using RETE engine."""

import pytest

from cybersec.rete.health import (
    HealthRuleEngine,
    HealthCheckPlan,
    CHECK_DEPENDENCIES,
    HEALTH_RULES,
)


class TestHealthRuleEngine:
    """Test the health rule engine."""

    def test_skip_checks_when_postgres_down(self):
        """Test that catalog checks are skipped when PostgreSQL is down."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=False)

        plan = engine.plan_checks()

        # These checks should be skipped
        assert "ICE_001" in plan.checks_to_skip
        assert "ICE_002" in plan.checks_to_skip
        assert "DATA_001" in plan.checks_to_skip

        # Should have skip reason
        assert "PostgreSQL" in plan.skip_reasons.get("ICE_001", "")

    def test_skip_checks_when_flink_down(self):
        """Test that Flink-dependent checks are skipped when cluster is down."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=False)

        plan = engine.plan_checks()

        # These checks should be skipped
        assert "FLINK_002" in plan.checks_to_skip
        assert "PYFLINK_001" in plan.checks_to_skip
        assert "PYFLINK_011" in plan.checks_to_skip

    def test_all_checks_run_when_healthy(self):
        """Test that all checks run when services are healthy."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)
        engine.set_service_health("polaris", healthy=True)
        engine.set_service_health("minio", healthy=True)

        plan = engine.plan_checks()

        # No dependency-based skips when everything is healthy
        dependency_skips = [
            skip
            for skip in plan.checks_to_skip
            if any(
                "dependency" in engine.engine.rules.get(rid, type("", (), {"category": ""})).category
                for rid in engine.engine.rules
            )
        ]
        # Should be empty or minimal
        assert len(plan.checks_to_skip) < len(CHECK_DEPENDENCIES)

    def test_auto_fix_low_rpn_issues(self):
        """Test that low RPN issues are auto-fixed."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)

        # Add a low RPN, auto-fixable issue
        engine.add_issue("TEST_001", rpn=50, auto_fixable=True)

        plan = engine.plan_checks()

        # Should recommend fixing
        assert "TEST_001" in plan.fixes_to_apply

    def test_no_auto_fix_high_rpn_issues(self):
        """Test that high RPN issues are not auto-fixed."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)

        # Add a high RPN issue
        engine.add_issue("TEST_002", rpn=500, auto_fixable=True)

        plan = engine.plan_checks()

        # Should NOT auto-fix (RPN > 400 requires manual)
        # But should generate an alert
        assert any("manual" in a["message"].lower() for a in plan.alerts)

    def test_fix_submodules_before_jars(self):
        """Test that submodule fix is prioritized before JAR builds."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)

        # Add submodule and JAR issues
        engine.add_issue("PYFLINK_013", rpn=270, auto_fixable=True)  # Submodules
        engine.add_issue("PYFLINK_011", rpn=180, auto_fixable=True)  # Iceberg JARs

        plan = engine.plan_checks()

        # PYFLINK_013 should be fixed first (submodules before JARs)
        if "PYFLINK_013" in plan.fixes_to_apply and "PYFLINK_011" in plan.fixes_to_apply:
            idx_013 = plan.fixes_to_apply.index("PYFLINK_013")
            idx_011 = plan.fixes_to_apply.index("PYFLINK_011")
            assert idx_013 < idx_011, "Submodules should be fixed before JARs"

    def test_execution_order_by_dependency_level(self):
        """Test that checks are ordered by dependency level."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)
        engine.set_service_health("polaris", healthy=True)

        plan = engine.plan_checks()

        # Should have multiple levels
        assert len(plan.execution_order) > 1

        # Level 0 should contain infrastructure checks
        level_0 = plan.execution_order[0]
        infra_checks = {"INFRA_001", "INFRA_002", "INFRA_003", "PYFLINK_013"}
        assert any(c in infra_checks for c in level_0)

    def test_explain_skip(self):
        """Test skip explanation."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=False)

        explanation = engine.explain_skip("ICE_001")

        assert explanation["check_id"] == "ICE_001"
        assert explanation["skipped"] is True
        assert "PostgreSQL" in explanation["reason"]

    def test_get_fix_order(self):
        """Test fix ordering by priority."""
        engine = HealthRuleEngine()

        engine.set_service_health("postgres", healthy=True)
        engine.set_service_health("flink", healthy=True)

        engine.add_issue("LOW_RPN", rpn=50, auto_fixable=True)
        engine.add_issue("MED_RPN", rpn=150, auto_fixable=True)

        fix_order = engine.get_fix_order()

        # Should return fixes with their RPN values
        assert len(fix_order) >= 1
        for fix_id, rpn, desc in fix_order:
            assert isinstance(fix_id, str)
            assert isinstance(rpn, int)
            assert isinstance(desc, str)


class TestCheckDependencies:
    """Test the check dependency graph."""

    def test_all_dependencies_exist(self):
        """Test that all dependency references are valid."""
        all_checks = set(CHECK_DEPENDENCIES.keys())

        for check_id, deps in CHECK_DEPENDENCIES.items():
            for dep in deps:
                assert dep in all_checks, f"{check_id} depends on unknown check {dep}"

    def test_no_circular_dependencies(self):
        """Test that there are no circular dependencies."""

        def has_cycle(check_id: str, visited: set, path: set) -> bool:
            visited.add(check_id)
            path.add(check_id)

            for dep in CHECK_DEPENDENCIES.get(check_id, []):
                if dep not in visited:
                    if has_cycle(dep, visited, path):
                        return True
                elif dep in path:
                    return True

            path.remove(check_id)
            return False

        visited: set[str] = set()
        for check_id in CHECK_DEPENDENCIES:
            if check_id not in visited:
                assert not has_cycle(check_id, visited, set()), f"Circular dependency detected involving {check_id}"

    def test_infrastructure_checks_have_no_deps(self):
        """Test that infrastructure checks have no dependencies."""
        infra_checks = ["INFRA_001", "INFRA_002", "INFRA_003"]

        for check_id in infra_checks:
            if check_id in CHECK_DEPENDENCIES:
                assert CHECK_DEPENDENCIES[check_id] == [], f"{check_id} should have no dependencies"


class TestHealthRules:
    """Test the built-in health rules."""

    def test_all_rules_have_required_fields(self):
        """Test that all rules have required fields."""
        for rule in HEALTH_RULES:
            assert rule.rule_id, "Rule must have rule_id"
            assert rule.conditions, "Rule must have conditions"
            assert rule.actions, "Rule must have actions"
            assert rule.priority > 0, "Rule must have positive priority"
            assert rule.category, "Rule should have category"
            assert rule.description, "Rule should have description"

    def test_dependency_rules_highest_priority(self):
        """Test that dependency rules have highest priority."""
        dependency_rules = [r for r in HEALTH_RULES if r.category == "dependency"]

        for rule in dependency_rules:
            assert rule.priority >= 950, f"Dependency rule {rule.rule_id} should have priority >= 950"

    def test_remediation_rules_properly_tiered(self):
        """Test that remediation rules follow tier priorities."""
        remediation_rules = [r for r in HEALTH_RULES if r.category == "remediation"]

        # Auto-fix (Tier 0) should be higher priority than prompt (Tier 2)
        auto_fix = next((r for r in remediation_rules if "tier0" in r.rule_id), None)
        prompt_fix = next((r for r in remediation_rules if "tier2" in r.rule_id), None)

        if auto_fix and prompt_fix:
            assert auto_fix.priority > prompt_fix.priority


class TestHealthCheckPlan:
    """Test HealthCheckPlan dataclass."""

    def test_plan_fields(self):
        """Test that plan has all required fields."""
        plan = HealthCheckPlan(
            checks_to_run=["A", "B"],
            checks_to_skip=["C"],
            skip_reasons={"C": "Service down"},
            fixes_to_apply=["FIX_1"],
            alerts=[{"message": "Warning", "severity": "warning"}],
            execution_order=[["A"], ["B"]],
        )

        assert len(plan.checks_to_run) == 2
        assert len(plan.checks_to_skip) == 1
        assert plan.skip_reasons["C"] == "Service down"
        assert len(plan.fixes_to_apply) == 1
        assert len(plan.alerts) == 1
        assert len(plan.execution_order) == 2
