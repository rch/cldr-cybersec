"""Tests for RETE engine built on OR-Tools CP-SAT."""

import pytest

from cybersec.rete.engine import ReteEngine
from cybersec.rete.facts import Fact, FactType, service_fact, issue_fact, table_fact
from cybersec.rete.rules import Rule, Condition, Action, Operator, when, then


class TestReteEngine:
    """Test the core RETE engine."""

    def test_assert_fact_and_query(self):
        """Test basic fact assertion."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "postgres", healthy=True, port=5438))

        assert "service.postgres" in engine.facts
        assert engine.facts["service.postgres"].attributes["healthy"] is True
        assert engine.facts["service.postgres"].attributes["port"] == 5438

    def test_simple_rule_fires(self):
        """Test that a simple rule fires when conditions match."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "postgres", healthy=True))

        engine.add_rule(
            Rule(
                "test_healthy",
                conditions=when(("service.postgres.healthy", "==", True)),
                actions=then(Action.alert("PostgreSQL is healthy")),
                priority=100,
            )
        )

        result = engine.solve()

        assert result.success
        assert len(result.activations) == 1
        assert result.activations[0].rule_id == "test_healthy"

    def test_rule_does_not_fire_when_conditions_fail(self):
        """Test that a rule doesn't fire when conditions don't match."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "postgres", healthy=False))

        engine.add_rule(
            Rule(
                "test_healthy",
                conditions=when(("service.postgres.healthy", "==", True)),
                actions=then(Action.alert("PostgreSQL is healthy")),
                priority=100,
            )
        )

        result = engine.solve()

        assert result.success
        # Rule should not fire because healthy=False
        matching = [a for a in result.activations if a.rule_id == "test_healthy"]
        assert len(matching) == 0

    def test_multiple_conditions_and(self):
        """Test that all conditions must be true for rule to fire."""
        engine = ReteEngine()

        engine.assert_fact(Fact("issue", "TEST_001", rpn=50, auto_fixable=True))

        engine.add_rule(
            Rule(
                "auto_fix_low_rpn",
                conditions=when(
                    ("issue.TEST_001.rpn", "<=", 100),
                    ("issue.TEST_001.auto_fixable", "==", True),
                ),
                actions=then(Action.fix("TEST_001")),
                priority=500,
            )
        )

        result = engine.solve()

        assert result.success
        assert len(result.activations) == 1
        assert result.activations[0].actions[0].action_type == "fix"

    def test_multiple_conditions_partial_match(self):
        """Test that partial condition matches don't fire the rule."""
        engine = ReteEngine()

        # RPN is low but not auto_fixable
        engine.assert_fact(Fact("issue", "TEST_001", rpn=50, auto_fixable=False))

        engine.add_rule(
            Rule(
                "auto_fix_low_rpn",
                conditions=when(
                    ("issue.TEST_001.rpn", "<=", 100),
                    ("issue.TEST_001.auto_fixable", "==", True),
                ),
                actions=then(Action.fix("TEST_001")),
                priority=500,
            )
        )

        result = engine.solve()

        assert result.success
        matching = [a for a in result.activations if a.rule_id == "auto_fix_low_rpn"]
        assert len(matching) == 0

    def test_priority_ordering(self):
        """Test that activations are ordered by priority."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "test", value=1))

        engine.add_rule(
            Rule(
                "low_priority",
                conditions=when(("service.test.value", "==", 1)),
                actions=then(Action.alert("low")),
                priority=100,
            )
        )
        engine.add_rule(
            Rule(
                "high_priority",
                conditions=when(("service.test.value", "==", 1)),
                actions=then(Action.alert("high")),
                priority=900,
            )
        )
        engine.add_rule(
            Rule(
                "medium_priority",
                conditions=when(("service.test.value", "==", 1)),
                actions=then(Action.alert("medium")),
                priority=500,
            )
        )

        result = engine.solve()

        assert result.success
        assert len(result.activations) == 3
        # Should be ordered high, medium, low
        assert result.activations[0].rule_id == "high_priority"
        assert result.activations[1].rule_id == "medium_priority"
        assert result.activations[2].rule_id == "low_priority"

    def test_wildcard_matching(self):
        """Test wildcard pattern matching in conditions."""
        engine = ReteEngine()

        engine.assert_fact(Fact("issue", "FLINK_001", rpn=150))
        engine.assert_fact(Fact("issue", "FLINK_002", rpn=80))
        engine.assert_fact(Fact("issue", "ICE_001", rpn=200))

        engine.add_rule(
            Rule(
                "fix_low_rpn_issues",
                conditions=when(("issue.*.rpn", "<=", 100)),
                actions=then(Action.fix("{issue}")),
                priority=500,
            )
        )

        result = engine.solve()

        assert result.success
        # Should only match FLINK_002 (rpn=80)
        assert len(result.activations) == 1
        assert "FLINK_002" in result.activations[0].bindings.values()

    def test_comparison_operators(self):
        """Test various comparison operators."""
        engine = ReteEngine()

        engine.assert_fact(Fact("table", "test", file_count=100, avg_size=25))

        # Test <=
        engine.add_rule(
            Rule(
                "small_files",
                conditions=when(("table.test.avg_size", "<=", 32)),
                actions=then(Action.compact("test")),
                priority=100,
            )
        )

        # Test >
        engine.add_rule(
            Rule(
                "many_files",
                conditions=when(("table.test.file_count", ">", 50)),
                actions=then(Action.rewrite_manifests("test")),
                priority=90,
            )
        )

        # Test >= (should not match)
        engine.add_rule(
            Rule(
                "huge_table",
                conditions=when(("table.test.file_count", ">=", 1000)),
                actions=then(Action.alert("huge")),
                priority=80,
            )
        )

        result = engine.solve()

        assert result.success
        rule_ids = {a.rule_id for a in result.activations}
        assert "small_files" in rule_ids  # avg_size=25 <= 32
        assert "many_files" in rule_ids  # file_count=100 > 50
        assert "huge_table" not in rule_ids  # file_count=100 < 1000

    def test_explain_rule(self):
        """Test rule explanation functionality."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "postgres", healthy=False, port=5438))

        engine.add_rule(
            Rule(
                "postgres_healthy",
                conditions=when(("service.postgres.healthy", "==", True)),
                actions=then(Action.alert("PostgreSQL is healthy")),
                priority=100,
                description="Check if PostgreSQL is healthy",
            )
        )

        explanation = engine.explain("postgres_healthy")

        assert explanation["rule_id"] == "postgres_healthy"
        assert explanation["fired"] is False
        assert len(explanation["conditions"]) == 1
        assert explanation["conditions"][0]["expected"] is True
        assert explanation["conditions"][0]["actual"] is False
        assert explanation["conditions"][0]["satisfied"] is False

    def test_retract_fact(self):
        """Test fact retraction."""
        engine = ReteEngine()

        engine.assert_fact(Fact("service", "postgres", healthy=True))

        engine.add_rule(
            Rule(
                "test_healthy",
                conditions=when(("service.postgres.healthy", "==", True)),
                actions=then(Action.alert("healthy")),
                priority=100,
            )
        )

        # First solve - should fire
        result1 = engine.solve()
        assert len(result1.activations) == 1

        # Retract and re-solve
        engine.retract_fact("service.postgres")
        result2 = engine.solve()

        # Rule should no longer fire (fact is absent)
        matching = [a for a in result2.activations if a.rule_id == "test_healthy"]
        assert len(matching) == 0


class TestConditions:
    """Test condition building and evaluation."""

    def test_condition_parts(self):
        """Test condition pattern parsing."""
        cond = Condition("service.postgres.healthy", Operator.EQ, True)
        parts = cond.parts

        assert parts == ("service", "postgres", "healthy")

    def test_condition_shorthand(self):
        """Test condition shorthand in when()."""
        conditions = when(
            ("issue.*.rpn", "<=", 100),
            ("issue.*.auto_fixable", "==", True),
        )

        assert len(conditions) == 2
        assert conditions[0].pattern == "issue.*.rpn"
        assert conditions[0].operator == Operator.LE
        assert conditions[0].value == 100


class TestActions:
    """Test action building and formatting."""

    def test_action_binding_substitution(self):
        """Test variable substitution in actions."""
        action = Action.fix("{issue}")
        bindings = {"issue": "FLINK_001"}

        formatted = action.format(bindings)

        assert formatted.target == "FLINK_001"

    def test_action_factory_methods(self):
        """Test action factory methods."""
        assert Action.fix("TEST_001").action_type == "fix"
        assert Action.skip("ICE_001").action_type == "skip"
        assert Action.compact("cloudtrail").action_type == "compact"
        assert Action.expire_snapshots("events", older_than_days=7).action_type == "expire_snapshots"


class TestFactHelpers:
    """Test fact helper functions."""

    def test_service_fact(self):
        """Test service fact creation."""
        fact = service_fact("postgres", healthy=True, port=5438)

        assert fact.fact_type == "service"
        assert fact.fact_id == "postgres"
        assert fact.attributes["healthy"] is True
        assert fact.attributes["port"] == 5438

    def test_issue_fact(self):
        """Test issue fact creation."""
        fact = issue_fact(
            "FLINK_001",
            rpn=280,
            severity=8,
            occurrence=7,
            detection=5,
            auto_fixable=True,
            tier=2,
        )

        assert fact.fact_type == "issue"
        assert fact.fact_id == "FLINK_001"
        assert fact.attributes["rpn"] == 280
        assert fact.attributes["auto_fixable"] is True

    def test_table_fact(self):
        """Test table fact creation."""
        fact = table_fact(
            "cloudtrail",
            row_count=10_000_000,
            partition_count=30,
            file_count=500,
            total_size_mb=2048,
            avg_file_size_mb=4,
        )

        assert fact.fact_type == "table"
        assert fact.fact_id == "cloudtrail"
        assert fact.attributes["row_count"] == 10_000_000
        assert fact.attributes["needs_compaction"] is True  # avg < 32MB


class TestSolveResult:
    """Test solve result properties."""

    def test_solve_time_recorded(self):
        """Test that solve time is recorded."""
        engine = ReteEngine()
        engine.assert_fact(Fact("test", "fact", value=1))

        result = engine.solve()

        assert result.solve_time_ms > 0
        assert result.num_facts == 1

    def test_success_property(self):
        """Test success property."""
        engine = ReteEngine()
        engine.assert_fact(Fact("test", "fact", value=1))

        result = engine.solve()

        assert result.success
        assert result.status in ("optimal", "feasible")
