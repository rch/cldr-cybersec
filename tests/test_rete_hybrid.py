"""Tests for hybrid inference engine."""

import pytest

from cybersec.rete.hybrid import (
    HybridEngine,
    HybridResult,
    InferenceMode,
    create_diagnostic_engine,
)
from cybersec.rete.backward import Goal, ProofStatus
from cybersec.rete.facts import Fact
from cybersec.rete.rules import Rule, Action, when, then


class TestHybridEngine:
    """Test hybrid inference engine."""

    def test_infer_with_all_facts_present(self):
        """Test inference when all facts are available."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "system_ready",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        engine.assert_fact(Fact("service", "postgres", healthy=True))
        engine.assert_fact(Fact("service", "flink", healthy=True))

        result = engine.infer("system_ready")

        assert result.status == ProofStatus.PROVEN
        assert result.goal_id == "system_ready"

    def test_infer_with_fact_acquisition(self):
        """Test inference with fact acquisition callback."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
                ("service.b.healthy", "==", True),
            ]
        ))

        # Start with one fact
        engine.assert_fact(Fact("service", "a", healthy=True))

        # Acquisition callback
        def acquire(pattern: str) -> Fact | None:
            if "service.b" in pattern:
                return Fact("service", "b", healthy=True)
            return None

        result = engine.infer("test", acquire_fact=acquire)

        assert result.status == ProofStatus.PROVEN
        assert "service.b" in result.facts_acquired

    def test_infer_stops_on_disproven(self):
        """Test that inference stops when goal is disproven."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
            ]
        ))

        engine.assert_fact(Fact("service", "a", healthy=False))

        result = engine.infer("test")

        assert result.status == ProofStatus.DISPROVEN
        # Should stop early, not continue acquiring facts
        assert len(result.steps) <= 3

    def test_infer_respects_max_steps(self):
        """Test that inference respects max_steps limit."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("check.a.ok", "==", True),
                ("check.b.ok", "==", True),
                ("check.c.ok", "==", True),
            ]
        ))

        # Callback that always returns None (simulating failed acquisition)
        def acquire(pattern: str) -> Fact | None:
            return None

        result = engine.infer("test", acquire_fact=acquire, max_steps=3)

        assert len(result.steps) <= 4  # max_steps + 1 for initial analysis

    def test_infer_tracks_acquisition_cost(self):
        """Test that inference tracks total acquisition cost."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.expensive.ok", "==", True),
                ("service.cheap.ok", "==", True),
            ]
        ))

        engine.set_acquisition_cost("service.expensive.ok", 10)
        engine.set_acquisition_cost("service.cheap.ok", 1)

        acquired_patterns = []

        def acquire(pattern: str) -> Fact | None:
            acquired_patterns.append(pattern)
            if "expensive" in pattern:
                return Fact("service", "expensive", ok=True)
            elif "cheap" in pattern:
                return Fact("service", "cheap", ok=True)
            return None

        result = engine.infer("test", acquire_fact=acquire)

        assert result.status == ProofStatus.PROVEN
        assert result.total_acquisition_cost == 11  # 10 + 1


class TestHybridForwardChaining:
    """Test forward chaining in hybrid engine."""

    def test_forward_rules_fire(self):
        """Test that forward chaining rules fire."""
        engine = HybridEngine()

        # Add a rule
        engine.add_rule(Rule(
            "propagate_status",
            conditions=when(("service.postgres.healthy", "==", False)),
            actions=then(Action.skip("ICE_001")),
            priority=100,
        ))

        engine.add_goal(Goal(
            "test",
            conditions=[("service.postgres.healthy", "==", True)]
        ))

        engine.assert_fact(Fact("service", "postgres", healthy=False))

        result = engine.infer("test")

        # Rule should have fired
        assert "propagate_status" in result.rules_fired

    def test_hybrid_combines_forward_and_backward(self):
        """Test that hybrid mode uses both chaining directions."""
        engine = HybridEngine()

        # Forward rule that infers a fact
        engine.add_rule(Rule(
            "infer_ready",
            conditions=when(
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ),
            actions=then(Action.alert("Infrastructure ready")),
            priority=100,
        ))

        # Backward goal
        engine.add_goal(Goal(
            "ready",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        engine.assert_fact(Fact("service", "postgres", healthy=True))
        engine.assert_fact(Fact("service", "flink", healthy=True))

        result = engine.infer("ready", mode=InferenceMode.HYBRID)

        # Should use both
        assert result.status == ProofStatus.PROVEN
        # Forward rule should fire
        assert any("infer" in r.lower() for r in result.rules_fired)


class TestHybridExplanation:
    """Test explanation capabilities in hybrid engine."""

    def test_explain_goal(self):
        """Test explanation generation."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
                ("service.b.healthy", "==", True),
            ],
            description="Test goal"
        ))

        engine.assert_fact(Fact("service", "a", healthy=True))
        engine.assert_fact(Fact("service", "b", healthy=True))

        explanation = engine.explain_goal("test")

        assert explanation.conclusion == ProofStatus.PROVEN
        assert len(explanation.steps) > 0

    def test_analyze_gaps(self):
        """Test gap analysis."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("check.a.ok", "==", True),
                ("check.b.ok", "==", True),
            ]
        ))

        gaps = engine.analyze_gaps("test")

        assert gaps.status == ProofStatus.UNKNOWN
        assert len(gaps.missing_facts) == 2

    def test_what_if(self):
        """Test hypothetical reasoning."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[("service.x.ok", "==", True)]
        ))

        explanation = engine.what_if(
            "test",
            [Fact("service", "x", ok=True)]
        )

        assert explanation.conclusion == ProofStatus.PROVEN
        assert "[HYPOTHETICAL]" in explanation.summary

    def test_get_proof_tree(self):
        """Test proof tree generation."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.ok", "==", True),
                ("service.b.ok", "==", True),
            ]
        ))

        engine.assert_fact(Fact("service", "a", ok=True))

        tree = engine.get_proof_tree("test")

        assert tree.node_id == "test"
        assert len(tree.children) == 2
        assert tree.children[0].status == ProofStatus.PROVEN
        assert tree.children[1].status == ProofStatus.UNKNOWN


class TestHybridResult:
    """Test HybridResult class."""

    def test_result_summary(self):
        """Test result summary generation."""
        result = HybridResult(
            goal_id="test",
            status=ProofStatus.PROVEN,
            steps=[],
            facts_acquired=["fact.a", "fact.b"],
            rules_fired=["rule1"],
            total_acquisition_cost=5,
        )

        summary = result.summary()

        assert "Goal: test" in summary
        assert "Status: proven" in summary
        assert "Facts acquired: 2" in summary
        assert "Total cost: 5" in summary


class TestDiagnosticEngine:
    """Test the pre-configured diagnostic engine."""

    def test_create_diagnostic_engine(self):
        """Test creation of diagnostic engine."""
        engine = create_diagnostic_engine()

        # Should have pre-configured goals
        assert "infrastructure_healthy" in engine.backward_engine.goals
        assert "flink_cluster_ready" in engine.backward_engine.goals
        assert "pyflink_ready" in engine.backward_engine.goals
        assert "full_pipeline_ready" in engine.backward_engine.goals

    def test_diagnostic_engine_infrastructure_goal(self):
        """Test infrastructure_healthy goal."""
        engine = create_diagnostic_engine()

        engine.assert_fact(Fact("service", "postgres", healthy=True))
        engine.assert_fact(Fact("service", "minio", healthy=True))
        engine.assert_fact(Fact("service", "polaris", healthy=True))

        result = engine.infer("infrastructure_healthy")

        assert result.status == ProofStatus.PROVEN

    def test_diagnostic_engine_gaps_when_service_down(self):
        """Test gap analysis when a service is down."""
        engine = create_diagnostic_engine()

        engine.assert_fact(Fact("service", "postgres", healthy=True))
        # minio and polaris not set

        gaps = engine.analyze_gaps("infrastructure_healthy")

        assert gaps.status == ProofStatus.UNKNOWN
        assert any("minio" in f for f in gaps.missing_facts)
        assert any("polaris" in f for f in gaps.missing_facts)


class TestInferenceModes:
    """Test different inference modes."""

    def test_forward_only_mode(self):
        """Test forward-only inference mode."""
        engine = HybridEngine()

        engine.add_rule(Rule(
            "test_rule",
            conditions=when(("service.a.healthy", "==", True)),
            actions=then(Action.alert("A is healthy")),
            priority=100,
        ))

        engine.add_goal(Goal(
            "test",
            conditions=[("service.a.healthy", "==", True)]
        ))

        engine.assert_fact(Fact("service", "a", healthy=True))

        result = engine.infer("test", mode=InferenceMode.FORWARD_ONLY, max_steps=3)

        # Forward rule should fire
        assert "test_rule" in result.rules_fired

    def test_backward_only_mode(self):
        """Test backward-only inference mode."""
        engine = HybridEngine()

        engine.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.ok", "==", True),
                ("service.b.ok", "==", True),
            ]
        ))

        acquired = []

        def acquire(pattern: str) -> Fact | None:
            acquired.append(pattern)
            if "service.a" in pattern:
                return Fact("service", "a", ok=True)
            elif "service.b" in pattern:
                return Fact("service", "b", ok=True)
            return None

        result = engine.infer("test", acquire_fact=acquire, mode=InferenceMode.BACKWARD_ONLY)

        assert result.status == ProofStatus.PROVEN
        assert len(acquired) == 2  # Both facts acquired
