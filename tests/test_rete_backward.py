"""Tests for backward chaining inference."""

import pytest

from cybersec.rete.backward import (
    BackwardChainer,
    Goal,
    ProofStatus,
    GapAnalysis,
    Explanation,
)
from cybersec.rete.facts import Fact


class TestBackwardChainer:
    """Test backward chaining engine."""

    def test_evaluate_goal_all_facts_present(self):
        """Test goal evaluation when all facts are available."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "system_healthy",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        chainer.assert_fact(Fact("service", "postgres", healthy=True))
        chainer.assert_fact(Fact("service", "flink", healthy=True))

        status = chainer.evaluate_goal("system_healthy")
        assert status == ProofStatus.PROVEN

    def test_evaluate_goal_missing_facts(self):
        """Test goal evaluation when facts are missing."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "system_healthy",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        # Only add one fact
        chainer.assert_fact(Fact("service", "postgres", healthy=True))

        status = chainer.evaluate_goal("system_healthy")
        assert status == ProofStatus.UNKNOWN

    def test_evaluate_goal_condition_fails(self):
        """Test goal evaluation when a condition fails."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "system_healthy",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        chainer.assert_fact(Fact("service", "postgres", healthy=True))
        chainer.assert_fact(Fact("service", "flink", healthy=False))  # Fails!

        status = chainer.evaluate_goal("system_healthy")
        assert status == ProofStatus.DISPROVEN


class TestGapAnalysis:
    """Test gap analysis functionality."""

    def test_analyze_gaps_finds_missing_facts(self):
        """Test that gap analysis identifies missing facts."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "pyflink_ready",
            conditions=[
                ("service.flink.healthy", "==", True),
                ("check.PYFLINK_001.is_ok", "==", True),
                ("check.PYFLINK_011.is_ok", "==", True),
            ]
        ))

        # Only add one fact
        chainer.assert_fact(Fact("service", "flink", healthy=True))

        gaps = chainer.analyze_gaps("pyflink_ready")

        assert gaps.status == ProofStatus.UNKNOWN
        assert "check.PYFLINK_001.is_ok" in gaps.missing_facts
        assert "check.PYFLINK_011.is_ok" in gaps.missing_facts
        assert len(gaps.missing_facts) == 2

    def test_analyze_gaps_creates_acquisition_plan(self):
        """Test that gap analysis creates an acquisition plan."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "ready",
            conditions=[
                ("check.A.is_ok", "==", True),
                ("check.B.is_ok", "==", True),
            ]
        ))

        # Set different costs
        chainer.set_acquisition_cost("check.A.is_ok", 10)
        chainer.set_acquisition_cost("check.B.is_ok", 1)

        gaps = chainer.analyze_gaps("ready")

        # Acquisition plan should be sorted by cost
        assert len(gaps.acquisition_plan) == 2
        assert gaps.acquisition_plan[0]["fact_pattern"] == "check.B.is_ok"  # Lower cost first
        assert gaps.acquisition_plan[1]["fact_pattern"] == "check.A.is_ok"

    def test_analyze_gaps_no_gaps_when_proven(self):
        """Test that there are no gaps when goal is proven."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "simple",
            conditions=[
                ("service.test.healthy", "==", True),
            ]
        ))

        chainer.assert_fact(Fact("service", "test", healthy=True))

        gaps = chainer.analyze_gaps("simple")

        assert gaps.status == ProofStatus.PROVEN
        assert len(gaps.missing_facts) == 0

    def test_analyze_gaps_identifies_blocking_conditions(self):
        """Test that blocking conditions are identified."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
                ("service.b.healthy", "==", True),
            ]
        ))

        # One fact satisfies, one fails
        chainer.assert_fact(Fact("service", "a", healthy=True))
        chainer.assert_fact(Fact("service", "b", healthy=False))

        gaps = chainer.analyze_gaps("test")

        assert gaps.status == ProofStatus.DISPROVEN
        assert len(gaps.blocking_conditions) == 1
        assert gaps.blocking_conditions[0]["actual"] is False


class TestProofTree:
    """Test proof tree generation."""

    def test_build_proof_tree(self):
        """Test proof tree construction."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test_goal",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ],
            description="Both services healthy"
        ))

        chainer.assert_fact(Fact("service", "postgres", healthy=True))

        tree = chainer.build_proof_tree("test_goal")

        assert tree.node_id == "test_goal"
        assert tree.node_type == "goal"
        assert tree.status == ProofStatus.UNKNOWN  # Missing flink fact
        assert len(tree.children) == 2

        # First condition should be proven
        assert tree.children[0].status == ProofStatus.PROVEN
        # Second should be unknown
        assert tree.children[1].status == ProofStatus.UNKNOWN

    def test_proof_tree_to_dict(self):
        """Test proof tree serialization."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[("service.a.healthy", "==", True)]
        ))

        chainer.assert_fact(Fact("service", "a", healthy=True))

        tree = chainer.build_proof_tree("test")
        tree_dict = tree.to_dict()

        assert tree_dict["node_id"] == "test"
        assert tree_dict["status"] == "proven"
        assert "children" in tree_dict


class TestExplanation:
    """Test explanation generation."""

    def test_explain_proven_goal(self):
        """Test explanation for a proven goal."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
                ("service.b.healthy", "==", True),
            ],
            description="Both services healthy"
        ))

        chainer.assert_fact(Fact("service", "a", healthy=True))
        chainer.assert_fact(Fact("service", "b", healthy=True))

        explanation = chainer.explain("test")

        assert explanation.conclusion == ProofStatus.PROVEN
        assert "PROVEN" in explanation.summary
        assert len(explanation.steps) >= 3  # start + 2 checks + conclude

    def test_explain_disproven_goal(self):
        """Test explanation for a disproven goal."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
            ]
        ))

        chainer.assert_fact(Fact("service", "a", healthy=False))

        explanation = chainer.explain("test")

        assert explanation.conclusion == ProofStatus.DISPROVEN
        assert "DISPROVEN" in explanation.summary
        # Should mention the failed condition
        assert any("FAILED" in str(s) for s in explanation.steps)

    def test_explain_unknown_goal(self):
        """Test explanation for an unknown goal."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
            ]
        ))

        # No facts added

        explanation = chainer.explain("test")

        assert explanation.conclusion == ProofStatus.UNKNOWN
        assert "UNKNOWN" in explanation.summary
        assert any("UNKNOWN" in str(s) for s in explanation.steps)

    def test_explanation_str_format(self):
        """Test that explanation has readable string format."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[("service.a.healthy", "==", True)],
            description="Test goal"
        ))

        chainer.assert_fact(Fact("service", "a", healthy=True))

        explanation = chainer.explain("test")
        explanation_str = str(explanation)

        assert "Goal: test" in explanation_str
        assert "Conclusion:" in explanation_str
        assert "Reasoning trace:" in explanation_str


class TestWhatIf:
    """Test hypothetical reasoning."""

    def test_what_if_adds_hypothetical_facts(self):
        """Test what-if analysis with hypothetical facts."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("service.a.healthy", "==", True),
                ("service.b.healthy", "==", True),
            ]
        ))

        # Only add one real fact
        chainer.assert_fact(Fact("service", "a", healthy=True))

        # Ask what-if with hypothetical fact
        explanation = chainer.what_if(
            "test",
            [Fact("service", "b", healthy=True)]
        )

        assert explanation.conclusion == ProofStatus.PROVEN
        assert "[HYPOTHETICAL]" in explanation.summary

        # Original facts should not be modified
        status = chainer.evaluate_goal("test")
        assert status == ProofStatus.UNKNOWN  # Still unknown without hypothetical

    def test_what_if_lower_confidence(self):
        """Test that hypothetical explanations have lower confidence."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal("test", conditions=[("x.y.z", "==", True)]))

        explanation = chainer.what_if("test", [Fact("x", "y", z=True)])

        assert explanation.confidence < 1.0


class TestSuggestNextAction:
    """Test action suggestion."""

    def test_suggest_next_action_returns_cheapest(self):
        """Test that suggestion returns the cheapest action."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal(
            "test",
            conditions=[
                ("check.expensive.is_ok", "==", True),
                ("check.cheap.is_ok", "==", True),
            ]
        ))

        chainer.set_acquisition_cost("check.expensive.is_ok", 100)
        chainer.set_acquisition_cost("check.cheap.is_ok", 1)

        suggestion = chainer.suggest_next_action("test")

        assert suggestion is not None
        assert suggestion["action"] == "acquire"
        assert suggestion["fact_pattern"] == "check.cheap.is_ok"

    def test_suggest_next_action_none_when_proven(self):
        """Test that no action is suggested when goal is proven."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal("test", conditions=[("x.y.z", "==", True)]))
        chainer.assert_fact(Fact("x", "y", z=True))

        suggestion = chainer.suggest_next_action("test")

        assert suggestion["action"] == "none"
        assert "proven" in suggestion["reason"]

    def test_suggest_next_action_none_when_disproven(self):
        """Test that no action is suggested when goal is disproven."""
        chainer = BackwardChainer()

        chainer.add_goal(Goal("test", conditions=[("x.y.z", "==", True)]))
        chainer.assert_fact(Fact("x", "y", z=False))

        suggestion = chainer.suggest_next_action("test")

        assert suggestion["action"] == "none"
        assert "cannot be proven" in suggestion["reason"]
