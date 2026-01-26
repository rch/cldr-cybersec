"""
Rule definitions for RETE engine.

Rules consist of:
- Conditions (LHS): Patterns to match against working memory
- Actions (RHS): What to do when the rule fires
- Priority: For conflict resolution (higher = fires first)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Operator(Enum):
    """Comparison operators for conditions."""

    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"  # Glob pattern for fact_id


@dataclass
class Condition:
    """
    A single condition in a rule's LHS.

    Conditions match against fact attributes using the pattern:
        {fact_type}.{fact_id}.{attribute} {operator} {value}

    The fact_id can use wildcards:
        - "*" matches any single fact_id
        - "prefix*" matches fact_ids starting with prefix

    Examples:
        Condition("service.postgres.healthy", Operator.EQ, True)
        Condition("issue.*.rpn", Operator.LE, 100)
        Condition("table.cloudtrail.avg_file_size_mb", Operator.LT, 32)
    """

    pattern: str  # fact_type.fact_id.attribute or fact_type.*.attribute
    operator: Operator | str
    value: Any
    # For variable binding (captures matched fact_id)
    bind_to: str | None = None

    def __post_init__(self):
        if isinstance(self.operator, str):
            self.operator = Operator(self.operator)

    @property
    def parts(self) -> tuple[str, str, str]:
        """Split pattern into (fact_type, fact_id_pattern, attribute)."""
        parts = self.pattern.split(".")
        if len(parts) == 3:
            return tuple(parts)  # type: ignore
        elif len(parts) == 2:
            # Assume fact_type.attribute with wildcard fact_id
            return (parts[0], "*", parts[1])
        else:
            raise ValueError(f"Invalid pattern: {self.pattern}")

    def __repr__(self) -> str:
        bind = f" as {self.bind_to}" if self.bind_to else ""
        return f"Condition({self.pattern!r} {self.operator.value} {self.value!r}{bind})"


@dataclass
class Action:
    """
    An action to perform when a rule fires.

    Actions can reference bound variables from conditions using {var_name}.

    Types:
        - "fix:{failure_mode_id}" - Apply a fix
        - "skip:{check_id}" - Skip a health check
        - "compact:{table_name}" - Trigger compaction
        - "evolve:{table_name}:{partition_spec}" - Evolve partition
        - "alert:{message}" - Emit an alert
        - "set:{fact_pattern}:{value}" - Modify working memory
    """

    action_type: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fix(cls, failure_mode_id: str, **params) -> "Action":
        return cls("fix", failure_mode_id, params)

    @classmethod
    def skip(cls, check_id: str) -> "Action":
        return cls("skip", check_id)

    @classmethod
    def compact(cls, table_name: str, **params) -> "Action":
        return cls("compact", table_name, params)

    @classmethod
    def evolve_partition(cls, table_name: str, new_spec: str) -> "Action":
        return cls("evolve_partition", table_name, {"new_spec": new_spec})

    @classmethod
    def rewrite_manifests(cls, table_name: str) -> "Action":
        return cls("rewrite_manifests", table_name)

    @classmethod
    def expire_snapshots(cls, table_name: str, older_than_days: int = 7) -> "Action":
        return cls("expire_snapshots", table_name, {"older_than_days": older_than_days})

    @classmethod
    def alert(cls, message: str, severity: str = "warning") -> "Action":
        return cls("alert", message, {"severity": severity})

    @classmethod
    def set_fact(cls, fact_pattern: str, value: Any) -> "Action":
        return cls("set", fact_pattern, {"value": value})

    def format(self, bindings: dict[str, str]) -> "Action":
        """Substitute bound variables in target and params."""
        target = self.target
        for var, val in bindings.items():
            target = target.replace(f"{{{var}}}", val)

        params = {}
        for k, v in self.params.items():
            if isinstance(v, str):
                for var, val in bindings.items():
                    v = v.replace(f"{{{var}}}", val)
            params[k] = v

        return Action(self.action_type, target, params)

    def __repr__(self) -> str:
        if self.params:
            return f"Action({self.action_type}:{self.target}, {self.params})"
        return f"Action({self.action_type}:{self.target})"


@dataclass
class Rule:
    """
    A production rule with conditions (LHS) and actions (RHS).

    Rules fire when ALL conditions are satisfied. Priority determines
    order of execution when multiple rules can fire (higher = first).

    Salience groups:
        1000+ : Infrastructure/dependency rules (run first)
        500-999: Remediation rules
        100-499: Optimization rules
        1-99: Informational/logging rules

    Example:
        Rule(
            "skip_checks_when_postgres_down",
            conditions=[
                Condition("service.postgres.healthy", "==", False)
            ],
            actions=[
                Action.skip("ICE_002"),
                Action.skip("DATA_001"),
            ],
            priority=1000,
            description="Skip catalog checks when PostgreSQL is unavailable"
        )
    """

    rule_id: str
    conditions: list[Condition]
    actions: list[Action]
    priority: int = 100
    description: str = ""
    enabled: bool = True
    # Optional: only fire once per unique binding
    fire_once: bool = False
    # Optional: category for grouping
    category: str = ""

    def __post_init__(self):
        # Convert shorthand conditions
        normalized = []
        for c in self.conditions:
            if isinstance(c, tuple):
                # (pattern, op, value) shorthand
                normalized.append(Condition(c[0], c[1], c[2]))
            else:
                normalized.append(c)
        self.conditions = normalized

        # Convert shorthand actions
        if isinstance(self.actions, Action):
            self.actions = [self.actions]
        elif isinstance(self.actions, str):
            # Parse "action_type:target" shorthand
            parts = self.actions.split(":", 1)
            self.actions = [Action(parts[0], parts[1] if len(parts) > 1 else "")]

    def __repr__(self) -> str:
        return f"Rule({self.rule_id!r}, priority={self.priority})"


# --- Convenience functions for rule creation ---


def when(*conditions) -> list[Condition]:
    """Create a list of conditions from shorthand tuples."""
    result = []
    for c in conditions:
        if isinstance(c, Condition):
            result.append(c)
        elif isinstance(c, tuple) and len(c) == 3:
            result.append(Condition(c[0], c[1], c[2]))
        elif isinstance(c, tuple) and len(c) == 4:
            result.append(Condition(c[0], c[1], c[2], bind_to=c[3]))
        else:
            raise ValueError(f"Invalid condition: {c}")
    return result


def then(*actions) -> list[Action]:
    """Create a list of actions."""
    result = []
    for a in actions:
        if isinstance(a, Action):
            result.append(a)
        elif isinstance(a, str):
            parts = a.split(":", 1)
            result.append(Action(parts[0], parts[1] if len(parts) > 1 else ""))
        else:
            raise ValueError(f"Invalid action: {a}")
    return result
