"""Lifecycle for the co-located ACP agent subprocess.

ONE shared agent process per engine pod, spawned LAZILY on first use (so the
engine boots without the agent), supervised, and closed gracefully within the
server's shutdown grace. The model endpoint (base_url) is injected via the child
env — never hardcoded; engine S3/AWS secrets are stripped from the child env so
they are never handed to the agent.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Never hand engine infrastructure secrets to the agent subprocess.
_STRIP_PREFIXES = ("AWS_", "S3_")
_STRIP_SUFFIXES = ("_SECRET", "_PASSWORD", "_SECRET_KEY", "_SECRET_ACCESS_KEY", "_TOKEN")


def _safe_child_env(cfg) -> dict:
    env = {}
    for k, v in os.environ.items():
        if any(k.startswith(p) for p in _STRIP_PREFIXES):
            continue
        if any(k.endswith(s) for s in _STRIP_SUFFIXES):
            continue
        env[k] = v
    # Point the agent at the local OpenAI-compatible endpoint (llama.cpp / vLLM).
    if cfg.model_base_url:
        env["OPENAI_BASE_URL"] = cfg.model_base_url
        env["OPENAI_API_BASE"] = cfg.model_base_url
    env["OPENAI_API_KEY"] = cfg.api_key or "EMPTY"
    if cfg.model_name:
        env["OPENAI_MODEL"] = cfg.model_name
        env["AGENT_MODEL"] = cfg.model_name
    return env


def _client_capabilities(acp, cfg):
    """Best-effort ClientCapabilities (fs read + optional write/terminal)."""
    with contextlib.suppress(Exception):
        fs_cap = acp.schema.FileSystemCapability(
            read_text_file=True,
            write_text_file=(cfg.allow_writes == "sandbox"),
        )
        return acp.schema.ClientCapabilities(fs=fs_cap, terminal=bool(cfg.allow_terminal))
    with contextlib.suppress(Exception):
        return acp.schema.ClientCapabilities()
    return None


class AcpSupervisor:
    """Owns the shared agent subprocess + ACP connection + session."""

    def __init__(self, cfg, client_factory: Callable):
        self._cfg = cfg
        self._client_factory = client_factory  # (acp_module) -> acp.Client
        self._lock = asyncio.Lock()
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._conn = None
        self._proc = None
        self._session_id: Optional[str] = None

    def _alive(self) -> bool:
        return (self._conn is not None and self._proc is not None
                and self._proc.returncode is None)

    async def connection(self) -> Tuple[object, str]:
        """Return (conn, session_id), spawning + initializing on first use."""
        if self._alive():
            return self._conn, self._session_id
        async with self._lock:
            if self._alive():
                return self._conn, self._session_id
            await self._spawn()
            return self._conn, self._session_id

    async def _spawn(self) -> None:
        import acp
        cmd = list(self._cfg.command or ["vibe-acp"])
        with contextlib.suppress(Exception):
            os.makedirs(self._cfg.sandbox_root, exist_ok=True)
        client = self._client_factory(acp)
        self._stack = contextlib.AsyncExitStack()
        cm = acp.spawn_agent_process(
            (lambda agent: client),
            cmd[0], *cmd[1:],
            env=_safe_child_env(self._cfg),
            cwd=self._cfg.sandbox_root if os.path.isdir(self._cfg.sandbox_root) else None,
        )
        try:
            self._conn, self._proc = await self._stack.enter_async_context(cm)
            await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=_client_capabilities(acp, self._cfg),
                client_info=acp.schema.Implementation(name="OTELNavigator", version="0.1.0"),
            )
            sess = await self._conn.new_session(cwd=self._cfg.sandbox_root, mcp_servers=[])
            self._session_id = sess.session_id
        except Exception:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None
            self._conn = None
            self._proc = None
            raise
        logger.info("ACP agent spawned (cmd=%s, session=%s)", cmd, self._session_id)

    async def health_check(self) -> bool:
        return self._alive()

    async def aclose(self) -> None:
        async with self._lock:
            # Terminate the subprocess FIRST so the stdio transport reader hits EOF
            # and its async generator finishes, otherwise closing the spawn context
            # races ("asynchronous generator is already running").
            proc = self._proc
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.terminate()
                with contextlib.suppress(Exception, asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=3)
            if self._stack is not None:
                with contextlib.suppress(Exception):
                    await self._stack.aclose()
            self._stack = None
            self._conn = None
            self._proc = None
            self._session_id = None
