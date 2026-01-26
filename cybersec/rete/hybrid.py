"""
Hybrid inference engine combining forward and backward chaining.

This engine combines the best of both approaches:
- **Backward chaining**: Goal-directed, identifies what's needed
- **Forward chaining (RETE)**: Data-driven, propagates implications efficiently

The hybrid approach enables:
1. Set a goal (e.g., "system ready for PyFlink jobs")
2. Backward chain to find what's unknown
3. Acquire facts incrementally
4. Forward chain to propagate implications
5. Re-evaluate goal status
6. Repeat until goal is proven/disproven or no more actions

This is particularly useful for:
- Intelligent diagnostics: Only check what's needed
- Explainable AI: Full reasoning trace from goal to facts
- Cost optimization: Minimize expensive checks
- Early termination: Stop when conclusion is certain
"""

from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum

from .engine import ReteEngine, Activation, SolveResult
from .backward import BackwardChainer, Goal, GapAnalysis, Explanation, ProofStatus, ProofNode
from .facts import Fact
from .rules import Rule


class InferenceMode(Enum):
    """Which inference direction to use."""
    FORWARD_ONLY = "forward"      # Pure RETE - data-driven
    BACKWARD_ONLY = "backward"    # Pure backward - goal-driven
    HYBRID = "hybrid"             # Combined approach


@dataclass
class InferenceStep:
    """A single step in the hybrid inference process."""
    step_number: int
    mode: str  # "backward", "forward", "acquire"
    action: str
    description: str
    facts_added: list[str] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)
    goal_status: ProofStatus = ProofStatus.UNKNOWN


@dataclass
class HybridResult:
    """Result of hybrid inference."""
    goal_id: str
    status: ProofStatus
    steps: list[InferenceStep]
    facts_acquired: list[str]
    rules_fired: list[str]
    forward_result: SolveResult | None = None
    backward_gaps: GapAnalysis | None = None
    explanation: Explanation | None = None
    total_acquisition_cost: int = 0

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            f"Goal: {self.goal_id}",
            f"Status: {self.status.value}",
            f"Steps taken: {len(self.steps)}",
            f"Facts acquired: {len(self.facts_acquired)}",
            f"Rules fired: {len(self.rules_fired)}",
            f"Total cost: {self.total_acquisition_cost}",
            "",
            "Inference trace:",
        ]
        for step in self.steps:
            lines.append(f"  {step.step_number}. [{step.mode.upper()}] {step.description}")
        return "\n".join(lines)


# Type alias for fact acquisition callback
FactAcquisitionCallback = Callable[[str], Fact | None]


class HybridEngine:
    """
    Hybrid inference engine combining RETE forward chaining with backward chaining.

    Usage:
        engine = HybridEngine()

        # Add rules for forward chaining
        engine.add_rule(Rule(...))

        # Add goals for backward chaining
        engine.add_goal(Goal(
            "pyflink_ready",
            conditions=[
                ("service.flink.healthy", "==", True),
                ("check.PYFLINK_011.is_ok", "==", True),
            ]
        ))

        # Define how to acquire facts
        def acquire_fact(pattern: str) -> Fact | None:
            if "service.flink" in pattern:
                # Actually check Flink
                healthy = check_flink_cluster()
                return Fact("service", "flink", healthy=healthy)
            return None

        # Run hybrid inference
        result = engine.infer("pyflink_ready", acquire_fact, max_steps=10)

        # Get explanation
        print(result.explanation)
    """

    def __init__(self):
        self.forward_engine = ReteEngine()
        self.backward_engine = BackwardChainer()
        self._acquisition_costs: dict[str, int] = {}

    def add_rule(self, rule: Rule) -> None:
        """Add a rule for forward chaining."""
        self.forward_engine.add_rule(rule)

    def add_goal(self, goal: Goal) -> None:
        """Add a goal for backward chaining."""
        self.backward_engine.add_goal(goal)

    def assert_fact(self, fact: Fact) -> None:
        """Add a fact to both engines."""
        self.forward_engine.assert_fact(fact)
        self.backward_engine.assert_fact(fact)

    def retract_fact(self, fact_key: str) -> None:
        """Remove a fact from both engines."""
        self.forward_engine.retract_fact(fact_key)
        self.backward_engine.retract_fact(fact_key)

    def set_acquisition_cost(self, fact_pattern: str, cost: int) -> None:
        """Set the cost to acquire a fact (for optimization)."""
        self._acquisition_costs[fact_pattern] = cost
        self.backward_engine.set_acquisition_cost(fact_pattern, cost)

    def infer(
        self,
        goal_id: str,
        acquire_fact: FactAcquisitionCallback | None = None,
        max_steps: int = 20,
        mode: InferenceMode = InferenceMode.HYBRID,
    ) -> HybridResult:
        """
        Run hybrid inference toward a goal.

        Args:
            goal_id: The goal to prove/disprove
            acquire_fact: Callback to acquire missing facts
            max_steps: Maximum inference steps
            mode: Which inference approach to use

        Returns:
            HybridResult with full reasoning trace
        """
        steps: list[InferenceStep] = []
        facts_acquired: list[str] = []
        rules_fired: list[str] = []
        total_cost = 0
        step_num = 0

        # Initial backward analysis
        gaps = self.backward_engine.analyze_gaps(goal_id)
        current_status = gaps.status

        steps.append(InferenceStep(
            step_number=step_num,
            mode="backward",
            action="analyze",
            description=f"Initial gap analysis: {len(gaps.missing_facts)} facts needed",
            goal_status=current_status,
        ))
        step_num += 1

        # Main inference loop
        while step_num < max_steps and current_status == ProofStatus.UNKNOWN:
            if mode == InferenceMode.FORWARD_ONLY:
                # Pure forward chaining
                result = self._forward_step(step_num)
                steps.append(result.step)
                rules_fired.extend(result.rules_fired)
                step_num += 1

            elif mode == InferenceMode.BACKWARD_ONLY:
                # Pure backward chaining with acquisition
                if acquire_fact and gaps.acquisition_plan:
                    next_fact = gaps.acquisition_plan[0]
                    result = self._acquire_step(
                        step_num, next_fact["fact_pattern"], acquire_fact
                    )
                    steps.append(result.step)
                    if result.fact_acquired:
                        facts_acquired.append(result.fact_acquired)
                        total_cost += next_fact.get("cost", 1)
                    step_num += 1
                else:
                    break  # No more facts to acquire

            else:  # HYBRID
                # 1. Try forward chaining first
                forward_result = self._forward_step(step_num)
                if forward_result.rules_fired:
                    steps.append(forward_result.step)
                    rules_fired.extend(forward_result.rules_fired)
                    step_num += 1

                    # Re-analyze gaps after forward chaining
                    gaps = self.backward_engine.analyze_gaps(goal_id)
                    current_status = gaps.status

                    steps.append(InferenceStep(
                        step_number=step_num,
                        mode="backward",
                        action="re-analyze",
                        description=f"After forward chain: {len(gaps.missing_facts)} facts still needed",
                        goal_status=current_status,
                    ))
                    step_num += 1
                    continue

                # 2. If forward didn't help, try acquisition
                if acquire_fact and gaps.acquisition_plan:
                    next_fact = gaps.acquisition_plan[0]
                    result = self._acquire_step(
                        step_num, next_fact["fact_pattern"], acquire_fact
                    )
                    steps.append(result.step)
                    if result.fact_acquired:
                        facts_acquired.append(result.fact_acquired)
                        total_cost += next_fact.get("cost", 1)
                    step_num += 1
                else:
                    # No forward rules fired and no facts to acquire
                    break

            # Re-evaluate goal status
            gaps = self.backward_engine.analyze_gaps(goal_id)
            current_status = gaps.status

        # Final forward chain to capture any implications
        final_forward = self.forward_engine.solve()
        for activation in final_forward.activations:
            if activation.rule_id not in rules_fired:
                rules_fired.append(activation.rule_id)

        # Generate explanation
        explanation = self.backward_engine.explain(goal_id)

        return HybridResult(
            goal_id=goal_id,
            status=current_status,
            steps=steps,
            facts_acquired=facts_acquired,
            rules_fired=rules_fired,
            forward_result=final_forward,
            backward_gaps=gaps,
            explanation=explanation,
            total_acquisition_cost=total_cost,
        )

    @dataclass
    class _ForwardStepResult:
        step: InferenceStep
        rules_fired: list[str]

    def _forward_step(self, step_num: int) -> _ForwardStepResult:
        """Execute one forward chaining step."""
        result = self.forward_engine.solve()

        rules_fired = [a.rule_id for a in result.activations]

        step = InferenceStep(
            step_number=step_num,
            mode="forward",
            action="propagate",
            description=f"Forward chain: {len(rules_fired)} rules fired",
            rules_fired=rules_fired,
        )

        return self._ForwardStepResult(step=step, rules_fired=rules_fired)

    @dataclass
    class _AcquireStepResult:
        step: InferenceStep
        fact_acquired: str | None

    def _acquire_step(
        self,
        step_num: int,
        fact_pattern: str,
        acquire_fact: FactAcquisitionCallback,
    ) -> _AcquireStepResult:
        """Acquire a missing fact."""
        fact = acquire_fact(fact_pattern)

        if fact:
            self.assert_fact(fact)
            step = InferenceStep(
                step_number=step_num,
                mode="acquire",
                action="get_fact",
                description=f"Acquired: {fact.key} = {fact.attributes}",
                facts_added=[fact.key],
            )
            return self._AcquireStepResult(step=step, fact_acquired=fact.key)
        else:
            step = InferenceStep(
                step_number=step_num,
                mode="acquire",
                action="get_fact",
                description=f"Failed to acquire: {fact_pattern}",
            )
            return self._AcquireStepResult(step=step, fact_acquired=None)

    def explain_goal(self, goal_id: str) -> Explanation:
        """Get explanation for a goal."""
        return self.backward_engine.explain(goal_id)

    def analyze_gaps(self, goal_id: str) -> GapAnalysis:
        """Analyze what's missing for a goal."""
        return self.backward_engine.analyze_gaps(goal_id)

    def what_if(self, goal_id: str, hypothetical_facts: list[Fact]) -> Explanation:
        """Explain what would happen if certain facts were true."""
        return self.backward_engine.what_if(goal_id, hypothetical_facts)

    def get_proof_tree(self, goal_id: str) -> ProofNode:
        """Get the proof tree for visualization."""
        return self.backward_engine.build_proof_tree(goal_id)

    def suggest_next_action(self, goal_id: str) -> dict[str, Any] | None:
        """Suggest the best next action."""
        return self.backward_engine.suggest_next_action(goal_id)


# --- Convenience functions ---

def create_diagnostic_engine() -> HybridEngine:
    """
    Create a hybrid engine configured for system diagnostics.

    Pre-configured with common health check goals and rules.
    """
    from .health import HEALTH_RULES, CHECK_DEPENDENCIES

    engine = HybridEngine()

    # Add forward chaining rules
    for rule in HEALTH_RULES:
        engine.add_rule(rule)

    # Add common diagnostic goals
    engine.add_goal(Goal(
        "infrastructure_healthy",
        conditions=[
            ("service.postgres.healthy", "==", True),
            ("service.minio.healthy", "==", True),
            ("service.polaris.healthy", "==", True),
        ],
        description="All infrastructure services are healthy",
    ))

    engine.add_goal(Goal(
        "flink_cluster_ready",
        conditions=[
            ("service.flink.healthy", "==", True),
            ("check.FLINK_001.is_ok", "==", True),
            ("check.FLINK_002.is_ok", "==", True),
        ],
        description="Flink cluster is ready for job submission",
    ))

    engine.add_goal(Goal(
        "pyflink_ready",
        conditions=[
            ("service.flink.healthy", "==", True),
            ("check.PYFLINK_001.is_ok", "==", True),
            ("check.PYFLINK_002.is_ok", "==", True),
            ("check.PYFLINK_011.is_ok", "==", True),
            ("check.PYFLINK_012.is_ok", "==", True),
        ],
        description="PyFlink environment is ready for job submission",
    ))

    engine.add_goal(Goal(
        "iceberg_catalog_ready",
        conditions=[
            ("service.postgres.healthy", "==", True),
            ("service.polaris.healthy", "==", True),
            ("check.ICE_001.is_ok", "==", True),
            ("check.ICE_002.is_ok", "==", True),
        ],
        description="Iceberg catalog is accessible and configured",
    ))

    engine.add_goal(Goal(
        "full_pipeline_ready",
        conditions=[
            ("service.postgres.healthy", "==", True),
            ("service.minio.healthy", "==", True),
            ("service.polaris.healthy", "==", True),
            ("service.flink.healthy", "==", True),
            ("check.ICE_002.is_ok", "==", True),
            ("check.PYFLINK_011.is_ok", "==", True),
        ],
        description="Full data pipeline is ready (Flink + Iceberg + Infrastructure)",
    ))

    # Set acquisition costs (more expensive checks have higher cost)
    engine.set_acquisition_cost("service.postgres.healthy", 1)  # Fast port check
    engine.set_acquisition_cost("service.minio.healthy", 1)
    engine.set_acquisition_cost("service.polaris.healthy", 2)  # API call
    engine.set_acquisition_cost("service.flink.healthy", 2)
    engine.set_acquisition_cost("check.ICE_001.is_ok", 5)  # DB query
    engine.set_acquisition_cost("check.ICE_002.is_ok", 5)
    engine.set_acquisition_cost("check.PYFLINK_011.is_ok", 3)  # File system check
    engine.set_acquisition_cost("check.PYFLINK_012.is_ok", 3)

    return engine
