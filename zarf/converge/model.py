"""Data model for the convergence engine: invariants, outcomes, probe/fix results.

Stdlib only. An *invariant* is one target-state fact with a read-only ``detect``
and (for Layer-B only) an idempotent ``remediate``. The catalog is an ordered list
of these; the engine reconciles them to a fixpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple


class Layer(str, Enum):
    """Which world-layer an invariant concerns.

    A = transported artifact (transport-once). HARD-protected: the engine never
        remediates a Layer-A invariant by deleting/fetching — it can only report
        and hand the operator a precise manual hint. CONSERVATION + CLOSURE.
    B = the deployment (deploy-many). Disposable: freely rebuilt from Layer A.
    """

    A = "A"
    B = "B"


class Cost(str, Enum):
    """Wall-time class of an invariant's remediation.

    EXPENSIVE remediations (``zarf init``, the 2 GB image push) are skipped
    whenever ``detect()`` already reports OK — the "transport once, deploy many"
    payoff. CHEAP ones are safe to attempt freely.
    """

    CHEAP = "cheap"
    EXPENSIVE = "expensive"


class Outcome(str, Enum):
    OK = "ok"                # already satisfied
    REMEDIATED = "fixed"     # was broken; engine repaired it (re-detect passed)
    WOULD_FIX = "would-fix"  # dry-run: broken, engine *would* repair
    MANUAL = "manual"        # broken; only an operator can fix (Layer A / no remediate)
    BLOCKED = "blocked"      # a dependency is not yet satisfied
    FAILED = "failed"        # remediation ran but re-detect still fails
    SKIPPED = "skipped"      # not evaluated this pass


@dataclass
class Probe:
    """Result of a read-only ``detect()``."""

    ok: bool
    detail: str = ""


@dataclass
class Fix:
    """Result of an idempotent ``remediate()``."""

    changed: bool = False
    detail: str = ""


# detect:    Callable[[Ctx], Probe]
# remediate: Callable[[Ctx], Fix]   (None => MANUAL only; always None for Layer A)
@dataclass(frozen=True)
class Invariant:
    id: str
    tier: str
    title: str
    layer: Layer
    detect: Callable
    remediate: Optional[Callable] = None
    cost: Cost = Cost.CHEAP
    depends_on: Tuple[str, ...] = ()
    manual_hint: str = ""

    def __post_init__(self):
        # CONSERVATION guard, enforced structurally: a Layer-A invariant must never
        # carry an automatic remediation. The engine therefore *cannot* mutate a
        # transported artifact — the prune/delete-StorageClass class of outage is
        # unrepresentable.
        if self.layer is Layer.A and self.remediate is not None:
            raise ValueError(
                f"invariant {self.id}: Layer-A invariants must be detect-only "
                "(remediate=None) — CONSERVATION forbids the engine mutating "
                "transported artifacts."
            )


@dataclass
class Eval:
    """One invariant's evaluation within a reconcile pass."""

    inv: Invariant
    outcome: Outcome
    detail: str = ""

    @property
    def converged(self) -> bool:
        return self.outcome in (Outcome.OK, Outcome.REMEDIATED)
