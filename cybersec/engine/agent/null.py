"""The default agent backend: a clean no-op.

Selected when ``AGENT_BACKEND`` is unset/"null". Imports NO ACP code and spawns
no process — the air-gap-without-model path depends on this being inert.
"""
from __future__ import annotations

from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb
from cybersec.engine.agent.base import (
    AgentSession,
    CancelToken,
    ClientCallbacks,
    agent_frame,
)

_DISABLED_MSG = (
    "AI agent is not enabled on this engine. Deterministic commands "
    "(status / set / load / query / export) are available — type 'help'."
)


class NullAgentSession(AgentSession):
    """Does nothing but report that the agent is disabled."""

    async def run_turn(
        self,
        prompt: str,
        *,
        turn_id: str,
        client_callbacks: ClientCallbacks,
        cancel_token: CancelToken,
    ) -> AsyncIterator[pb.AgentFrame]:
        yield agent_frame(
            turn_id,
            turn_complete=pb.TurnComplete(status="disabled", summary=_DISABLED_MSG),
        )

    async def health_check(self) -> bool:
        return True  # the null agent intentionally does nothing, so it's "healthy"
