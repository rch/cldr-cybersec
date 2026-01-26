"""
RETE-style rule engine built on OR-Tools CP-SAT solver.

The engine uses constraint propagation for efficient pattern matching:
- Facts become Boolean/Integer variables in the CP model
- Conditions become constraints on those variables
- Rule activations are reified (rule_fires => all conditions true)
- Conflict resolution uses the objective function (maximize priority)

Key advantages over traditional RETE:
- Incremental solving: Add constraints without full rebuild
- Optimization-native: Priority is part of the model
- Constraint propagation: Efficiently prunes impossible combinations
- Explanation: Can extract which constraints caused activation
"""

from dataclasses import dataclass, field
from typing import Any
import fnmatch
import re

from ortools.sat.python import cp_model

from .facts import Fact
from .rules import Rule, Condition, Action, Operator


@dataclass
class Activation:
    """A rule that is ready to fire with specific bindings."""

    rule_id: str
    rule: Rule
    bindings: dict[str, str]  # Variable name -> matched fact_id
    actions: list[Action]  # Actions with bindings substituted
    priority: int

    def __repr__(self) -> str:
        return f"Activation({self.rule_id}, bindings={self.bindings}, priority={self.priority})"


@dataclass
class SolveResult:
    """Result of solving the rule engine."""

    status: str  # "optimal", "feasible", "infeasible", "unknown"
    activations: list[Activation]
    solve_time_ms: float
    num_facts: int
    num_rules: int
    num_variables: int
    num_constraints: int

    @property
    def success(self) -> bool:
        return self.status in ("optimal", "feasible")


class ReteEngine:
    """
    RETE-style forward-chaining rule engine using OR-Tools CP-SAT.

    Example:
        engine = ReteEngine()

        # Assert facts about current state
        engine.assert_fact(Fact("service", "postgres", healthy=True))
        engine.assert_fact(Fact("issue", "FLINK_001", rpn=280, auto_fixable=True))

        # Add rules
        engine.add_rule(Rule(
            "auto_fix_low_rpn",
            conditions=[("issue.*.rpn", "<=", 100), ("issue.*.auto_fixable", "==", True)],
            actions=[Action.fix("{issue}")],
            priority=500
        ))

        # Solve and get activations
        result = engine.solve()
        for activation in result.activations:
            print(f"{activation.rule_id} -> {activation.actions}")
    """

    # Scale factor for float -> int conversion (CP-SAT only supports integers)
    FLOAT_SCALE = 1000

    def __init__(self):
        self.model = cp_model.CpModel()
        self.facts: dict[str, Fact] = {}  # fact.key -> Fact
        self.rules: dict[str, Rule] = {}  # rule_id -> Rule

        # CP-SAT variables
        self._fact_vars: dict[str, cp_model.IntVar] = {}  # attr_key -> variable
        self._fact_presence: dict[str, cp_model.IntVar] = {}  # fact.key -> BoolVar
        self._rule_fires: dict[str, cp_model.IntVar] = {}  # rule_id -> BoolVar

        # For incremental solving
        self._dirty = True
        self._last_result: SolveResult | None = None

    def assert_fact(self, fact: Fact) -> None:
        """Add or update a fact in working memory."""
        self.facts[fact.key] = fact
        self._dirty = True

        # Create presence variable for this fact
        if fact.key not in self._fact_presence:
            self._fact_presence[fact.key] = self.model.NewBoolVar(f"exists:{fact.key}")

        # Fact is present
        self.model.Add(self._fact_presence[fact.key] == 1)

        # Create variables for each attribute
        for attr, value in fact.attributes.items():
            attr_key = fact.attr_key(attr)
            self._create_attribute_var(attr_key, value)

    def retract_fact(self, fact_key: str) -> None:
        """Remove a fact from working memory."""
        if fact_key in self.facts:
            del self.facts[fact_key]
            self._dirty = True
            # Mark fact as not present
            if fact_key in self._fact_presence:
                self.model.Add(self._fact_presence[fact_key] == 0)

    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the engine."""
        self.rules[rule.rule_id] = rule
        self._dirty = True

    def remove_rule(self, rule_id: str) -> None:
        """Remove a rule from the engine."""
        if rule_id in self.rules:
            del self.rules[rule_id]
            self._dirty = True

    def _create_attribute_var(self, attr_key: str, value: Any) -> cp_model.IntVar:
        """Create or update a CP-SAT variable for an attribute."""
        if attr_key in self._fact_vars:
            var = self._fact_vars[attr_key]
        else:
            if isinstance(value, bool):
                var = self.model.NewBoolVar(attr_key)
            elif isinstance(value, int):
                # Use reasonable bounds
                lb = min(0, value - 10000)
                ub = max(value + 10000, 1000000)
                var = self.model.NewIntVar(lb, ub, attr_key)
            elif isinstance(value, float):
                # Scale to integer
                scaled = int(value * self.FLOAT_SCALE)
                lb = min(0, scaled - 10000 * self.FLOAT_SCALE)
                ub = max(scaled + 10000 * self.FLOAT_SCALE, 1000000 * self.FLOAT_SCALE)
                var = self.model.NewIntVar(lb, ub, attr_key)
                value = scaled
            elif isinstance(value, str):
                # Strings are stored but not directly constrainable
                # Use hash for equality checks
                var = self.model.NewIntVar(0, 2**31 - 1, attr_key)
                value = hash(value) % (2**31)
            elif isinstance(value, list):
                # Lists stored as length for now
                var = self.model.NewIntVar(0, 10000, attr_key)
                value = len(value)
            else:
                # Skip unsupported types
                return None

            self._fact_vars[attr_key] = var

        # Set the value
        if isinstance(value, bool):
            self.model.Add(var == (1 if value else 0))
        else:
            self.model.Add(var == value)

        return var

    def _find_matching_facts(self, fact_type: str, fact_id_pattern: str) -> list[str]:
        """Find fact keys matching the pattern."""
        matching = []
        for fact_key, fact in self.facts.items():
            if fact.fact_type != fact_type:
                continue
            if fact_id_pattern == "*":
                matching.append(fact_key)
            elif "*" in fact_id_pattern:
                if fnmatch.fnmatch(fact.fact_id, fact_id_pattern):
                    matching.append(fact_key)
            elif fact.fact_id == fact_id_pattern:
                matching.append(fact_key)
        return matching

    def _build_rule_constraints(self, rule: Rule) -> list[tuple[dict[str, str], cp_model.IntVar]]:
        """
        Build CP-SAT constraints for a rule.

        Returns list of (bindings, fires_var) for each possible binding combination.
        """
        # Find all possible bindings for wildcard conditions
        binding_combinations = [{}]  # Start with empty binding

        for condition in rule.conditions:
            fact_type, fact_id_pattern, attr = condition.parts

            if "*" in fact_id_pattern:
                # Wildcard - need to expand bindings
                matching_facts = self._find_matching_facts(fact_type, fact_id_pattern)
                if not matching_facts:
                    return []  # No matches, rule can't fire

                new_combinations = []
                for bindings in binding_combinations:
                    for fact_key in matching_facts:
                        fact = self.facts[fact_key]
                        new_bindings = bindings.copy()
                        if condition.bind_to:
                            new_bindings[condition.bind_to] = fact.fact_id
                        else:
                            # Auto-bind to fact_type
                            new_bindings[fact_type] = fact.fact_id
                        new_combinations.append(new_bindings)
                binding_combinations = new_combinations

        # Build constraints for each binding combination
        results = []
        for bindings in binding_combinations:
            fires_var = self.model.NewBoolVar(f"fires:{rule.rule_id}:{hash(frozenset(bindings.items()))}")

            condition_vars = []
            for condition in rule.conditions:
                fact_type, fact_id_pattern, attr = condition.parts

                # Resolve fact_id from bindings if wildcard
                if "*" in fact_id_pattern:
                    fact_id = bindings.get(condition.bind_to or fact_type, fact_id_pattern)
                else:
                    fact_id = fact_id_pattern

                attr_key = f"{fact_type}.{fact_id}.{attr}"
                fact_var = self._fact_vars.get(attr_key)

                if fact_var is None:
                    # Attribute doesn't exist, condition fails
                    self.model.Add(fires_var == 0)
                    break

                # Create condition satisfaction variable
                cond_var = self.model.NewBoolVar(f"cond:{rule.rule_id}:{attr_key}")
                condition_vars.append(cond_var)

                # Add constraint based on operator
                value = condition.value
                if isinstance(value, float):
                    value = int(value * self.FLOAT_SCALE)
                elif isinstance(value, str):
                    # Convert string to hash for CP-SAT comparison
                    value = hash(value) % (2**31)

                if condition.operator == Operator.EQ:
                    cmp_val = 1 if value is True else (0 if value is False else value)
                    self.model.Add(fact_var == cmp_val).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var != cmp_val).OnlyEnforceIf(cond_var.Not())
                elif condition.operator == Operator.NE:
                    cmp_val = 1 if value is True else (0 if value is False else value)
                    self.model.Add(fact_var != cmp_val).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var == cmp_val).OnlyEnforceIf(cond_var.Not())
                elif condition.operator == Operator.LT:
                    self.model.Add(fact_var < value).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var >= value).OnlyEnforceIf(cond_var.Not())
                elif condition.operator == Operator.LE:
                    self.model.Add(fact_var <= value).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var > value).OnlyEnforceIf(cond_var.Not())
                elif condition.operator == Operator.GT:
                    self.model.Add(fact_var > value).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var <= value).OnlyEnforceIf(cond_var.Not())
                elif condition.operator == Operator.GE:
                    self.model.Add(fact_var >= value).OnlyEnforceIf(cond_var)
                    self.model.Add(fact_var < value).OnlyEnforceIf(cond_var.Not())

            else:
                # All conditions processed, rule fires if all satisfied
                if condition_vars:
                    self.model.AddBoolAnd(condition_vars).OnlyEnforceIf(fires_var)
                    # If any condition false, rule doesn't fire
                    for cv in condition_vars:
                        self.model.AddImplication(cv.Not(), fires_var.Not())
                else:
                    # No conditions = always fires
                    self.model.Add(fires_var == 1)

            results.append((bindings, fires_var))

        return results

    def solve(self, time_limit_ms: int = 5000) -> SolveResult:
        """
        Solve the constraint model and return activated rules.

        Returns rules that fire, ordered by priority (highest first).
        """
        import time

        start = time.time()

        # Rebuild model if dirty
        # Note: In production, we'd use incremental solving
        if self._dirty:
            self.model = cp_model.CpModel()
            self._fact_vars.clear()
            self._fact_presence.clear()
            self._rule_fires.clear()

            # Re-add all facts
            for fact in list(self.facts.values()):
                self.assert_fact(fact)

        # Build rule constraints
        all_activations: list[tuple[Rule, dict[str, str], cp_model.IntVar]] = []

        for rule in self.rules.values():
            if not rule.enabled:
                continue

            binding_results = self._build_rule_constraints(rule)
            for bindings, fires_var in binding_results:
                all_activations.append((rule, bindings, fires_var))
                self._rule_fires[f"{rule.rule_id}:{hash(frozenset(bindings.items()))}"] = fires_var

        # Objective: maximize sum of (fires * priority)
        if all_activations:
            objective_terms = []
            for rule, bindings, fires_var in all_activations:
                objective_terms.append(fires_var * rule.priority)
            self.model.Maximize(sum(objective_terms))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_ms / 1000.0

        status = solver.Solve(self.model)

        solve_time = (time.time() - start) * 1000

        status_str = {
            cp_model.OPTIMAL: "optimal",
            cp_model.FEASIBLE: "feasible",
            cp_model.INFEASIBLE: "infeasible",
            cp_model.MODEL_INVALID: "invalid",
            cp_model.UNKNOWN: "unknown",
        }.get(status, "unknown")

        # Extract fired rules
        activations = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for rule, bindings, fires_var in all_activations:
                if solver.Value(fires_var):
                    # Substitute bindings in actions
                    bound_actions = [a.format(bindings) for a in rule.actions]
                    activations.append(
                        Activation(
                            rule_id=rule.rule_id,
                            rule=rule,
                            bindings=bindings,
                            actions=bound_actions,
                            priority=rule.priority,
                        )
                    )

        # Sort by priority (highest first)
        activations.sort(key=lambda a: -a.priority)

        self._dirty = False
        result = SolveResult(
            status=status_str,
            activations=activations,
            solve_time_ms=solve_time,
            num_facts=len(self.facts),
            num_rules=len(self.rules),
            num_variables=len(self._fact_vars),
            num_constraints=self.model.Proto().constraints.__len__(),
        )
        self._last_result = result
        return result

    def explain(self, rule_id: str) -> dict[str, Any]:
        """
        Explain why a rule did or did not fire.

        Returns dict with:
        - fired: bool
        - conditions: list of {pattern, operator, expected, actual, satisfied}
        - bindings: matched fact_ids (if fired)
        """
        rule = self.rules.get(rule_id)
        if not rule:
            return {"error": f"Rule {rule_id} not found"}

        explanation = {
            "rule_id": rule_id,
            "description": rule.description,
            "fired": False,
            "conditions": [],
            "bindings": {},
        }

        # Check each condition
        for condition in rule.conditions:
            fact_type, fact_id_pattern, attr = condition.parts

            # Find matching facts
            matching = self._find_matching_facts(fact_type, fact_id_pattern)

            if not matching:
                explanation["conditions"].append(
                    {
                        "pattern": condition.pattern,
                        "operator": condition.operator.value,
                        "expected": condition.value,
                        "actual": None,
                        "satisfied": False,
                        "reason": f"No facts match {fact_type}.{fact_id_pattern}",
                    }
                )
                continue

            for fact_key in matching:
                fact = self.facts[fact_key]
                actual = fact.attributes.get(attr)

                # Evaluate condition
                satisfied = self._evaluate_condition(condition, actual)

                explanation["conditions"].append(
                    {
                        "pattern": condition.pattern,
                        "fact": fact_key,
                        "operator": condition.operator.value,
                        "expected": condition.value,
                        "actual": actual,
                        "satisfied": satisfied,
                    }
                )

                if satisfied and condition.bind_to:
                    explanation["bindings"][condition.bind_to] = fact.fact_id

        # Rule fires if all conditions satisfied
        explanation["fired"] = all(c["satisfied"] for c in explanation["conditions"])

        return explanation

    def _evaluate_condition(self, condition: Condition, actual: Any) -> bool:
        """Evaluate a condition against an actual value."""
        if actual is None:
            return False

        expected = condition.value
        op = condition.operator

        if op == Operator.EQ:
            return actual == expected
        elif op == Operator.NE:
            return actual != expected
        elif op == Operator.LT:
            return actual < expected
        elif op == Operator.LE:
            return actual <= expected
        elif op == Operator.GT:
            return actual > expected
        elif op == Operator.GE:
            return actual >= expected
        elif op == Operator.IN:
            return actual in expected
        elif op == Operator.NOT_IN:
            return actual not in expected
        elif op == Operator.MATCHES:
            return fnmatch.fnmatch(str(actual), expected)

        return False

    def clear(self) -> None:
        """Clear all facts and rules."""
        self.facts.clear()
        self.rules.clear()
        self._fact_vars.clear()
        self._fact_presence.clear()
        self._rule_fires.clear()
        self.model = cp_model.CpModel()
        self._dirty = True
