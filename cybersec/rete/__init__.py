"""
RETE-style rule engine built on OR-Tools CP-SAT solver.

This module provides a forward-chaining rule engine that leverages OR-Tools'
constraint propagation for efficient pattern matching and conflict resolution.

Key concepts:
- Working Memory: Facts about the current system state
- Rules: IF-THEN patterns that fire when conditions match
- Activations: Rules that are ready to fire, prioritized by objective function

Example usage:
    from cybersec.rete import ReteEngine, Fact, Rule

    engine = ReteEngine()

    # Assert facts
    engine.assert_fact(Fact("service", "postgres", healthy=True, port=5438))
    engine.assert_fact(Fact("issue", "FLINK_001", rpn=280, auto_fixable=True))

    # Add rules
    engine.add_rule(Rule(
        "auto_fix_low_rpn",
        when=[("issue.*.rpn", "<=", 100), ("issue.*.auto_fixable", "==", True)],
        then="fix:{issue}",
        priority=500
    ))

    # Solve and get activations
    for activation in engine.solve():
        print(f"{activation.rule_id} -> {activation.action}")
"""

from .engine import ReteEngine, Activation, SolveResult
from .facts import Fact, FactType
from .rules import Rule, Condition, Action, when, then
from .iceberg import IcebergOptimizer, TableStats
from .health import HealthRuleEngine, HealthCheckPlan

__all__ = [
    # Core engine
    "ReteEngine",
    "Activation",
    "SolveResult",
    # Facts
    "Fact",
    "FactType",
    # Rules
    "Rule",
    "Condition",
    "Action",
    "when",
    "then",
    # Iceberg optimization
    "IcebergOptimizer",
    "TableStats",
    # Health checks
    "HealthRuleEngine",
    "HealthCheckPlan",
]
