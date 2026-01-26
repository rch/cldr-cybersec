"""
Backward chaining inference for goal-directed reasoning.

Backward chaining starts from a goal and works backwards to determine
what facts are needed to prove or disprove it. This is complementary
to forward chaining (RETE) which propagates facts forward to conclusions.

Key capabilities:
- Goal-directed inference: "What do I need to prove X?"
- Gap analysis: "Why can't I conclude X yet?"
- Acquisition planning: "What checks should I run, in what order?"
- Explanation: "How did I reach this conclusion?"

Example:
    engine = BackwardChainer()

    # Define what we want to prove
    engine.add_goal(Goal(
        "pyflink_can_run",
        conditions=[
            ("service.flink.healthy", "==", True),
            ("check.PYFLINK_001.is_ok", "==", True),
            ("check.PYFLINK_011.is_ok", "==", True),
        ]
    ))

    # Add known facts
    engine.assert_fact(Fact("service", "flink", healthy=True))

    # What's missing?
    gaps = engine.analyze_gaps("pyflink_can_run")
    # Returns: ["check.PYFLINK_001.is_ok", "check.PYFLINK_011.is_ok"]
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .facts import Fact
from .rules import Condition, Operator


class ProofStatus(Enum):
    """Status of a goal or sub-goal in the proof tree."""
    UNKNOWN = "unknown"      # Not enough information
    PROVEN = "proven"        # All conditions satisfied
    DISPROVEN = "disproven"  # At least one condition failed
    PENDING = "pending"      # Waiting for fact acquisition


@dataclass
class Goal:
    """
    A goal to prove or disprove via backward chaining.

    Goals consist of conditions that must all be true for the goal
    to be proven. Conditions can reference facts that may not yet
    exist in working memory.
    """
    goal_id: str
    conditions: list[Condition]
    description: str = ""
    # Optional: sub-goals that can alternatively prove this goal
    alternative_goals: list[str] = field(default_factory=list)
    # Optional: priority for acquisition planning
    priority: int = 100

    def __post_init__(self):
        # Convert tuple shorthand to Condition objects
        normalized = []
        for c in self.conditions:
            if isinstance(c, tuple) and len(c) >= 3:
                normalized.append(Condition(c[0], c[1], c[2]))
            elif isinstance(c, Condition):
                normalized.append(c)
            else:
                raise ValueError(f"Invalid condition: {c}")
        self.conditions = normalized


@dataclass
class ProofNode:
    """
    A node in the proof tree representing a condition or sub-goal.

    The proof tree shows the logical structure of how a goal can be
    proven, including what's known, unknown, and how facts relate.
    """
    node_id: str
    node_type: str  # "goal", "condition", "fact", "conjunction", "disjunction"
    status: ProofStatus
    description: str
    children: list["ProofNode"] = field(default_factory=list)
    # For conditions: the actual vs expected value
    expected_value: Any = None
    actual_value: Any = None
    # For facts: the source of the fact
    source: str = ""
    # Cost to acquire this fact (for optimization)
    acquisition_cost: int = 1

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "node_id": self.node_id,
            "type": self.node_type,
            "status": self.status.value,
            "description": self.description,
            "expected": self.expected_value,
            "actual": self.actual_value,
            "source": self.source,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class GapAnalysis:
    """
    Analysis of what's missing to prove a goal.
    """
    goal_id: str
    status: ProofStatus
    missing_facts: list[str]  # Fact patterns that are unknown
    blocking_conditions: list[dict]  # Conditions that can't be evaluated
    acquisition_plan: list[dict]  # Ordered list of facts to acquire
    proof_tree: ProofNode | None = None

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "status": self.status.value,
            "missing_facts": self.missing_facts,
            "blocking_conditions": self.blocking_conditions,
            "acquisition_plan": self.acquisition_plan,
            "proof_tree": self.proof_tree.to_dict() if self.proof_tree else None,
        }


@dataclass
class ExplanationStep:
    """A single step in an explanation trace."""
    step_number: int
    action: str  # "check", "infer", "conclude", "fail"
    description: str
    fact_pattern: str = ""
    result: str = ""

    def __str__(self) -> str:
        if self.result:
            return f"{self.step_number}. [{self.action.upper()}] {self.description} → {self.result}"
        return f"{self.step_number}. [{self.action.upper()}] {self.description}"


@dataclass
class Explanation:
    """
    Human-readable explanation of reasoning.

    Provides a trace of how a conclusion was reached (or why it couldn't be).
    """
    goal_id: str
    conclusion: ProofStatus
    summary: str
    steps: list[ExplanationStep]
    confidence: float = 1.0  # 0-1, lower if based on assumptions

    def __str__(self) -> str:
        lines = [
            f"Goal: {self.goal_id}",
            f"Conclusion: {self.conclusion.value}",
            f"Summary: {self.summary}",
            "",
            "Reasoning trace:",
        ]
        for step in self.steps:
            lines.append(f"  {step}")
        return "\n".join(lines)


class BackwardChainer:
    """
    Backward chaining inference engine.

    Works backwards from goals to determine what facts are needed,
    builds proof trees for explainability, and plans fact acquisition.

    Example:
        chainer = BackwardChainer()

        # Define goals
        chainer.add_goal(Goal(
            "system_healthy",
            conditions=[
                ("service.postgres.healthy", "==", True),
                ("service.flink.healthy", "==", True),
            ]
        ))

        # Add known facts
        chainer.assert_fact(Fact("service", "postgres", healthy=True))

        # Analyze gaps
        gaps = chainer.analyze_gaps("system_healthy")
        print(gaps.missing_facts)  # ["service.flink.healthy"]

        # Get explanation
        explanation = chainer.explain("system_healthy")
        print(explanation)
    """

    def __init__(self):
        self.goals: dict[str, Goal] = {}
        self.facts: dict[str, Fact] = {}  # fact.key -> Fact
        # Dependency graph: goal -> list of sub-goals/conditions
        self._dependencies: dict[str, list[str]] = {}
        # Acquisition costs for facts
        self._acquisition_costs: dict[str, int] = {}

    def add_goal(self, goal: Goal) -> None:
        """Add a goal that can be proven or disproven."""
        self.goals[goal.goal_id] = goal

    def assert_fact(self, fact: Fact) -> None:
        """Add or update a fact in working memory.

        If a fact with the same key exists, merges the new attributes
        with existing ones (new values override existing).
        """
        existing = self.facts.get(fact.key)
        if existing:
            # Merge attributes - new values override existing
            merged_attrs = {**existing.attributes, **fact.attributes}
            self.facts[fact.key] = Fact(fact.fact_type, fact.fact_id, **merged_attrs)
        else:
            self.facts[fact.key] = fact

    def retract_fact(self, fact_key: str) -> None:
        """Remove a fact from working memory."""
        if fact_key in self.facts:
            del self.facts[fact_key]

    def set_acquisition_cost(self, fact_pattern: str, cost: int) -> None:
        """Set the cost to acquire a particular fact (for optimization)."""
        self._acquisition_costs[fact_pattern] = cost

    def get_fact_value(self, pattern: str) -> tuple[bool, Any]:
        """
        Get the value of a fact attribute from working memory.

        Supports wildcard patterns like "fact_type.*.attribute" which
        matches any fact of the given type.

        Returns (exists, value) tuple.
        """
        parts = pattern.split(".")
        if len(parts) < 3:
            return False, None

        fact_type, fact_id, attr = parts[0], parts[1], ".".join(parts[2:])

        # Handle wildcard: find any matching fact of this type
        if fact_id == "*":
            for fact_key, fact in self.facts.items():
                if fact_key.startswith(f"{fact_type}."):
                    if attr in fact.attributes:
                        return True, fact.attributes[attr]
            return False, None

        # Exact match
        fact_key = f"{fact_type}.{fact_id}"
        fact = self.facts.get(fact_key)
        if fact is None:
            return False, None

        if attr in fact.attributes:
            return True, fact.attributes[attr]

        return False, None

    def evaluate_condition(self, condition: Condition) -> tuple[ProofStatus, Any]:
        """
        Evaluate a condition against working memory.

        Returns (status, actual_value).
        """
        exists, actual = self.get_fact_value(condition.pattern)

        if not exists:
            return ProofStatus.UNKNOWN, None

        # Evaluate the condition
        expected = condition.value
        op = condition.operator

        satisfied = False
        if op == Operator.EQ:
            satisfied = actual == expected
        elif op == Operator.NE:
            satisfied = actual != expected
        elif op == Operator.LT:
            satisfied = actual < expected
        elif op == Operator.LE:
            satisfied = actual <= expected
        elif op == Operator.GT:
            satisfied = actual > expected
        elif op == Operator.GE:
            satisfied = actual >= expected
        elif op == Operator.IN:
            satisfied = actual in expected
        elif op == Operator.NOT_IN:
            satisfied = actual not in expected

        if satisfied:
            return ProofStatus.PROVEN, actual
        else:
            return ProofStatus.DISPROVEN, actual

    def evaluate_goal(self, goal_id: str) -> ProofStatus:
        """
        Evaluate a goal's current status.

        Returns PROVEN if all conditions satisfied,
        DISPROVEN if any condition definitively failed,
        UNKNOWN if any condition can't be evaluated yet.
        """
        goal = self.goals.get(goal_id)
        if not goal:
            return ProofStatus.UNKNOWN

        has_unknown = False

        for condition in goal.conditions:
            status, _ = self.evaluate_condition(condition)

            if status == ProofStatus.DISPROVEN:
                return ProofStatus.DISPROVEN
            elif status == ProofStatus.UNKNOWN:
                has_unknown = True

        if has_unknown:
            return ProofStatus.UNKNOWN
        else:
            return ProofStatus.PROVEN

    def build_proof_tree(self, goal_id: str) -> ProofNode:
        """
        Build a proof tree showing the logical structure of the goal.

        The tree shows:
        - The goal as root
        - Conditions as children (conjunction)
        - Status of each node (proven/disproven/unknown)
        - Actual vs expected values
        """
        goal = self.goals.get(goal_id)
        if not goal:
            return ProofNode(
                node_id=goal_id,
                node_type="goal",
                status=ProofStatus.UNKNOWN,
                description=f"Goal '{goal_id}' not found",
            )

        # Build condition nodes
        condition_nodes = []
        overall_status = ProofStatus.PROVEN
        has_unknown = False

        for i, condition in enumerate(goal.conditions):
            status, actual = self.evaluate_condition(condition)

            node = ProofNode(
                node_id=f"{goal_id}.cond.{i}",
                node_type="condition",
                status=status,
                description=f"{condition.pattern} {condition.operator.value} {condition.value}",
                expected_value=condition.value,
                actual_value=actual,
                acquisition_cost=self._acquisition_costs.get(condition.pattern, 1),
            )
            condition_nodes.append(node)

            if status == ProofStatus.DISPROVEN:
                overall_status = ProofStatus.DISPROVEN
            elif status == ProofStatus.UNKNOWN:
                has_unknown = True

        if overall_status != ProofStatus.DISPROVEN and has_unknown:
            overall_status = ProofStatus.UNKNOWN

        return ProofNode(
            node_id=goal_id,
            node_type="goal",
            status=overall_status,
            description=goal.description or f"Goal: {goal_id}",
            children=condition_nodes,
        )

    def analyze_gaps(self, goal_id: str) -> GapAnalysis:
        """
        Analyze what's missing to prove a goal.

        Returns:
        - missing_facts: Fact patterns that are unknown
        - blocking_conditions: Conditions that can't be evaluated
        - acquisition_plan: Ordered list of facts to acquire
        """
        goal = self.goals.get(goal_id)
        if not goal:
            return GapAnalysis(
                goal_id=goal_id,
                status=ProofStatus.UNKNOWN,
                missing_facts=[],
                blocking_conditions=[],
                acquisition_plan=[],
            )

        missing_facts = []
        blocking_conditions = []

        for condition in goal.conditions:
            status, actual = self.evaluate_condition(condition)

            if status == ProofStatus.UNKNOWN:
                missing_facts.append(condition.pattern)
                blocking_conditions.append({
                    "pattern": condition.pattern,
                    "operator": condition.operator.value,
                    "expected": condition.value,
                    "reason": "Fact not in working memory",
                })
            elif status == ProofStatus.DISPROVEN:
                blocking_conditions.append({
                    "pattern": condition.pattern,
                    "operator": condition.operator.value,
                    "expected": condition.value,
                    "actual": actual,
                    "reason": "Condition not satisfied",
                })

        # Build acquisition plan (ordered by cost)
        acquisition_plan = []
        for fact_pattern in missing_facts:
            cost = self._acquisition_costs.get(fact_pattern, 1)
            acquisition_plan.append({
                "fact_pattern": fact_pattern,
                "cost": cost,
                "action": self._suggest_acquisition_action(fact_pattern),
            })

        # Sort by cost (cheapest first)
        acquisition_plan.sort(key=lambda x: x["cost"])

        # Build proof tree
        proof_tree = self.build_proof_tree(goal_id)

        return GapAnalysis(
            goal_id=goal_id,
            status=self.evaluate_goal(goal_id),
            missing_facts=missing_facts,
            blocking_conditions=blocking_conditions,
            acquisition_plan=acquisition_plan,
            proof_tree=proof_tree,
        )

    def _suggest_acquisition_action(self, fact_pattern: str) -> str:
        """Suggest how to acquire a missing fact."""
        parts = fact_pattern.split(".")
        if len(parts) < 2:
            return "unknown"

        fact_type = parts[0]

        if fact_type == "service":
            return f"Run health check for {parts[1]}"
        elif fact_type == "check":
            return f"Execute check {parts[1]}"
        elif fact_type == "issue":
            return f"Run diagnostics for {parts[1]}"
        elif fact_type == "table":
            return f"Query Iceberg metadata for {parts[1]}"
        elif fact_type == "config":
            return f"Read configuration {parts[1]}"
        else:
            return f"Acquire fact {fact_pattern}"

    def explain(self, goal_id: str) -> Explanation:
        """
        Generate a human-readable explanation of reasoning.

        Shows the step-by-step process of evaluating a goal,
        what's known, what's unknown, and the conclusion.
        """
        goal = self.goals.get(goal_id)
        if not goal:
            return Explanation(
                goal_id=goal_id,
                conclusion=ProofStatus.UNKNOWN,
                summary=f"Goal '{goal_id}' not defined",
                steps=[],
            )

        steps = []
        step_num = 1

        # Start with goal
        steps.append(ExplanationStep(
            step_number=step_num,
            action="start",
            description=f"Evaluating goal: {goal.description or goal_id}",
        ))
        step_num += 1

        # Evaluate each condition
        all_proven = True
        any_disproven = False

        for condition in goal.conditions:
            status, actual = self.evaluate_condition(condition)

            if status == ProofStatus.UNKNOWN:
                steps.append(ExplanationStep(
                    step_number=step_num,
                    action="check",
                    description=f"Checking {condition.pattern}",
                    fact_pattern=condition.pattern,
                    result="UNKNOWN - fact not available",
                ))
                all_proven = False
            elif status == ProofStatus.PROVEN:
                steps.append(ExplanationStep(
                    step_number=step_num,
                    action="check",
                    description=f"Checking {condition.pattern} {condition.operator.value} {condition.value}",
                    fact_pattern=condition.pattern,
                    result=f"SATISFIED (actual: {actual})",
                ))
            else:  # DISPROVEN
                steps.append(ExplanationStep(
                    step_number=step_num,
                    action="check",
                    description=f"Checking {condition.pattern} {condition.operator.value} {condition.value}",
                    fact_pattern=condition.pattern,
                    result=f"FAILED (actual: {actual})",
                ))
                all_proven = False
                any_disproven = True

            step_num += 1

        # Conclusion
        if any_disproven:
            conclusion = ProofStatus.DISPROVEN
            summary = f"Goal '{goal_id}' is DISPROVEN - one or more conditions failed"
        elif all_proven:
            conclusion = ProofStatus.PROVEN
            summary = f"Goal '{goal_id}' is PROVEN - all conditions satisfied"
        else:
            conclusion = ProofStatus.UNKNOWN
            summary = f"Goal '{goal_id}' is UNKNOWN - missing information needed"

        steps.append(ExplanationStep(
            step_number=step_num,
            action="conclude",
            description=summary,
        ))

        return Explanation(
            goal_id=goal_id,
            conclusion=conclusion,
            summary=summary,
            steps=steps,
        )

    def what_if(self, goal_id: str, hypothetical_facts: list[Fact]) -> Explanation:
        """
        Explain what would happen if certain facts were true.

        Useful for planning: "If I fix X, would the goal be achievable?"
        """
        # Temporarily add hypothetical facts
        original_facts = self.facts.copy()

        for fact in hypothetical_facts:
            self.assert_fact(fact)

        # Generate explanation with hypotheticals
        explanation = self.explain(goal_id)
        explanation.summary = f"[HYPOTHETICAL] {explanation.summary}"
        explanation.confidence = 0.8  # Lower confidence for hypotheticals

        # Restore original facts
        self.facts = original_facts

        return explanation

    def suggest_next_action(self, goal_id: str) -> dict[str, Any] | None:
        """
        Suggest the best next action to make progress toward the goal.

        Uses acquisition costs to recommend the most efficient next step.
        """
        gaps = self.analyze_gaps(goal_id)

        if gaps.status == ProofStatus.PROVEN:
            return {"action": "none", "reason": "Goal already proven"}

        if gaps.status == ProofStatus.DISPROVEN:
            return {"action": "none", "reason": "Goal cannot be proven - conditions failed"}

        if gaps.acquisition_plan:
            next_step = gaps.acquisition_plan[0]
            return {
                "action": "acquire",
                "fact_pattern": next_step["fact_pattern"],
                "suggested_action": next_step["action"],
                "cost": next_step["cost"],
                "remaining_unknowns": len(gaps.missing_facts) - 1,
            }

        return None
