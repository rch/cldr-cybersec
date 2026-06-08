"""Backend-agnostic agent session contract for the NavigatorEngine.

The engine federates an OPTIONAL Agent Client Protocol (ACP) agent over the
bidirectional gRPC ``AgentSession`` RPC. This module defines the contract
(mirroring the LLMBackend ABC pattern from the atelier reference): one
``AgentSession`` per logical conversation, driven a turn at a time, yielding
``AgentFrame``s. The default ``NullAgentSession`` (null.py) does nothing — so the
deterministic REPL works with no model attached (the air-gap baseline).
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from cybersec.engine.generated import navigator_pb2 as pb


class AgentUnavailable(RuntimeError):
    """A real backend is configured but its deps or subprocess are unavailable."""


def _never() -> bool:  # default cancel predicate
    return False


@dataclass
class CancelToken:
    """A cheap predicate a running turn polls for cooperative cancellation.

    Backed by the servicer's ``_active_commands`` map so Ctrl-C / the ``Cancel``
    RPC work the same as for the deterministic Execute path."""
    is_cancelled: Callable[[], bool] = _never

    @property
    def cancelled(self) -> bool:
        return bool(self.is_cancelled())


@dataclass
class ClientCallbacks:
    """How the engine resolves the ACP agent's mid-turn callbacks (permission, fs).

    v1: constructed from an engine-side ``AgentPolicy`` (resolved locally; no
    browser dialogs). v2: constructed to forward to the gRPC client and await a
    matching ``ClientFrame``. The ``AcpAgentSession`` code is identical across
    both — only how this object is constructed changes, which is what makes
    browser permission federation a later, non-breaking step.
    """
    request_permission: Optional[Callable[[pb.PermissionRequest], Awaitable[int]]] = None
    fs: Optional[Callable[[pb.FsRequest], Awaitable[pb.FsResponse]]] = None


def agent_frame(turn_id: str, **oneof: Any) -> pb.AgentFrame:
    """Build an ``AgentFrame`` with the timestamp stamped."""
    return pb.AgentFrame(turn_id=turn_id, timestamp_ms=int(time.time() * 1000), **oneof)


class AgentSession(ABC):
    """One logical agent conversation, federated over the gRPC AgentSession RPC."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @abstractmethod
    def run_turn(
        self,
        prompt: str,
        *,
        turn_id: str,
        client_callbacks: ClientCallbacks,
        cancel_token: CancelToken,
    ) -> AsyncIterator[pb.AgentFrame]:
        """Drive ONE prompt->completion turn, yielding AgentFrames as they stream."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """True if the agent (and its model endpoint) is reachable."""
        ...

    async def aclose(self) -> None:
        """Release resources (subprocess, channels). Default: no-op."""
        return None
