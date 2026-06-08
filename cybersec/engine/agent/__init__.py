"""Optional, vendor-agnostic agent infrastructure for the NavigatorEngine.

The agent is OFF by default (``NullAgentSession``). Real backends (e.g. ACP /
mistral-vibe) are imported LAZILY so the null path pulls in no ACP/httpx deps and
cannot fail to import.
"""
from __future__ import annotations

import logging

from cybersec.engine.agent.base import (
    AgentSession,
    AgentUnavailable,
    CancelToken,
    ClientCallbacks,
    agent_frame,
)
from cybersec.engine.agent.config import AgentConfig
from cybersec.engine.agent.null import NullAgentSession

logger = logging.getLogger(__name__)

__all__ = [
    "AgentSession",
    "AgentUnavailable",
    "CancelToken",
    "ClientCallbacks",
    "agent_frame",
    "AgentConfig",
    "NullAgentSession",
    "make_agent_session",
]


def make_agent_session(config: "AgentConfig | None" = None) -> AgentSession:
    """Construct the configured AgentSession (default: NullAgentSession).

    Importing a real backend is lazy, so a null engine never imports ACP/httpx.
    An unknown or broken backend fails SAFE to null rather than crashing the
    engine — the deterministic REPL must always come up.
    """
    cfg = config or AgentConfig.from_env()
    backend = cfg.backend.strip().lower()
    if not cfg.enabled:
        return NullAgentSession(cfg)
    if backend == "acp":
        try:
            from cybersec.engine.agent.acp import AcpAgentSession
        except Exception as e:  # acp.py absent (pre-P3) or an import error
            logger.warning("ACP backend unavailable (%s); falling back to null agent", e)
            return NullAgentSession(cfg)
        return AcpAgentSession(cfg)
    logger.warning("Unknown AGENT_BACKEND=%r; using null agent", cfg.backend)
    return NullAgentSession(cfg)
