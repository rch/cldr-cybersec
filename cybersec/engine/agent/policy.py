"""Engine-side agent policy (v1).

v1 resolves the ACP agent's permission/fs callbacks LOCALLY (no browser dialogs):
allow read/fetch/search within an allowlist of roots; deny writes (unless
``allow_writes=sandbox``, confined to the sandbox root) and terminals. The same
``allow_*`` seam is what a future browser-federated handler calls, so flipping to
client-side permission prompts is a non-breaking change.

This module is pure/deterministic and imports no ACP code, so it is unit-testable
without the agent installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

# ACP ToolKind literals considered read-only / safe to auto-allow.
_READONLY_KINDS = frozenset({"read", "fetch", "search", "think"})
_MUTATING_KINDS = frozenset({"edit", "delete", "move"})


@dataclass
class AgentPolicy:
    sandbox_root: str = "/tmp/agent-sandbox"
    dataset_roots: List[str] = field(default_factory=list)  # extra read-only roots
    allow_writes: str = "off"          # "off" | "sandbox"
    allow_terminal: bool = False

    def _read_roots(self) -> List[str]:
        roots = [self.sandbox_root, *self.dataset_roots]
        return [os.path.realpath(r) for r in roots if r]

    @staticmethod
    def _under(path: str, root: str) -> bool:
        rp = os.path.realpath(path)
        return rp == root or rp.startswith(root + os.sep)

    def allow_read(self, path: str) -> bool:
        return any(self._under(path, root) for root in self._read_roots())

    def allow_write(self, path: str) -> bool:
        if self.allow_writes != "sandbox":
            return False
        return self._under(path, os.path.realpath(self.sandbox_root))

    def allow_tool(self, kind: str) -> bool:
        """Permission decision for a tool call, keyed on its ACP ToolKind."""
        k = (kind or "").strip().lower()
        if k in _READONLY_KINDS:
            return True
        if k in _MUTATING_KINDS:
            return self.allow_writes == "sandbox"
        if k == "execute":
            return self.allow_terminal
        return False  # unknown / "other" → deny by default (least privilege)
