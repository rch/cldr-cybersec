"""ACP agent backend — federate a co-located ACP agent (e.g. mistral-vibe).

The engine is the ACP CLIENT: it spawns the agent subprocess over stdio (via the
AcpSupervisor) and implements the client callbacks (session_update, permission,
fs) here. All ACP imports are LOCAL/lazy so the null path never imports this.

Validated offline against a fake ACP agent (no model needed). Live validation:
``AGENT_BACKEND=acp AGENT_ACP_COMMAND=vibe-acp AGENT_MODEL_BASE_URL=<endpoint>``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import AsyncIterator, Optional

from cybersec.engine.generated import navigator_pb2 as pb
from cybersec.engine.agent.base import (
    AgentSession,
    AgentUnavailable,
    agent_frame,
)
from cybersec.engine.agent.policy import AgentPolicy
from cybersec.engine.agent.supervisor import AcpSupervisor

logger = logging.getLogger(__name__)


def _import_acp():
    try:
        import acp  # noqa: F401
        return acp
    except Exception as e:
        raise AgentUnavailable(
            "agent-client-protocol is not installed; build the engine with the "
            "'agent' extra or set AGENT_BACKEND=null"
        ) from e


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    text = getattr(content, "text", None)
    if text is not None:
        return text
    if isinstance(content, (list, tuple)):
        return "".join(_content_text(c) for c in content)
    return ""


def _update_to_frame(turn_id: str, update) -> "pb.AgentFrame | None":
    """Map an ACP SessionUpdate to an AgentFrame (None = ignored in v1)."""
    kind = getattr(update, "session_update", None)
    if kind == "agent_message_chunk":
        return agent_frame(turn_id, assistant_chunk=pb.AssistantChunk(
            text=_content_text(getattr(update, "content", None)), thinking=False))
    if kind == "agent_thought_chunk":
        return agent_frame(turn_id, assistant_chunk=pb.AssistantChunk(
            text=_content_text(getattr(update, "content", None)), thinking=True))
    if kind == "tool_call":
        return agent_frame(turn_id, tool_call=pb.ToolCall(
            tool_call_id=getattr(update, "tool_call_id", "") or "",
            title=getattr(update, "title", "") or "",
            kind=str(getattr(update, "kind", "") or "")))
    if kind == "tool_call_update":
        return agent_frame(turn_id, tool_call_update=pb.ToolCallUpdate(
            tool_call_id=getattr(update, "tool_call_id", "") or "",
            status=str(getattr(update, "status", "") or "")))
    return None  # plan/commands/mode/usage updates ignored in v1


def _read_text(path: str, line: Optional[int], limit: Optional[int]) -> str:
    with open(os.path.realpath(path), "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    start = (line - 1) if line else 0
    end = (start + limit) if limit else len(lines)
    return "".join(lines[start:end])


def _request_error(acp, message: str) -> Exception:
    """Build the most appropriate ACP error to refuse a callback; the framework
    converts a raised exception into a JSON-RPC error back to the agent."""
    RE = getattr(acp, "RequestError", None)
    if RE is not None:
        for ctor in ("invalid_request", "invalid_params", "internal_error"):
            fn = getattr(RE, ctor, None)
            if callable(fn):
                try:
                    return fn(message)
                except TypeError:
                    with contextlib.suppress(Exception):
                        return fn()
        with contextlib.suppress(Exception):
            return RE(message)
    return PermissionError(message)


def _make_nav_client(acp, session: "AcpAgentSession"):
    """Build the acp.Client subclass (deferred so this module imports w/o acp)."""
    policy = session._policy

    class _NavClient(acp.Client):
        async def session_update(self, session_id, update, **kw):
            frame = _update_to_frame(session._cur_turn, update)
            if frame is not None:
                session._deliver(frame)

        async def request_permission(self, options, session_id, tool_call, **kw):
            kind = getattr(tool_call, "kind", "") or ""
            if policy.allow_tool(kind):
                opt = next((o for o in options
                            if str(getattr(o, "kind", "")).startswith("allow")), None)
                if opt is not None:
                    return acp.RequestPermissionResponse(
                        outcome=acp.schema.AllowedOutcome(option_id=opt.option_id))
            return acp.RequestPermissionResponse(outcome=acp.schema.DeniedOutcome())

        async def read_text_file(self, path, session_id, limit=None, line=None, **kw):
            if not policy.allow_read(path):
                raise _request_error(acp, f"read denied (outside sandbox): {path}")
            return acp.ReadTextFileResponse(content=_read_text(path, line, limit))

        async def write_text_file(self, content, path, session_id, **kw):
            if not policy.allow_write(path):
                raise _request_error(acp, f"write denied: {path}")
            rp = os.path.realpath(path)
            os.makedirs(os.path.dirname(rp) or ".", exist_ok=True)
            with open(rp, "w", encoding="utf-8") as fh:
                fh.write(content)
            with contextlib.suppress(Exception):
                return acp.WriteTextFileResponse()
            return None

        async def create_terminal(self, command, session_id, args=None, cwd=None,
                                  env=None, output_byte_limit=None, **kw):
            raise _request_error(acp, "terminal access is disabled by policy")

    return _NavClient()


class AcpAgentSession(AgentSession):
    """Drives a co-located ACP agent over the supervisor's shared subprocess.

    v1 serializes turns (one model, one session) and resolves permission/fs
    callbacks from the engine-side AgentPolicy."""

    def __init__(self, config):
        super().__init__(config)
        self._policy = AgentPolicy(
            sandbox_root=config.sandbox_root,
            allow_writes=config.allow_writes,
            allow_terminal=config.allow_terminal,
        )
        self._supervisor = AcpSupervisor(config, self._build_client)
        self._turn_lock = asyncio.Lock()
        self._queue: "Optional[asyncio.Queue]" = None
        self._cur_turn: str = ""

    def _build_client(self, acp):
        return _make_nav_client(acp, self)

    def _deliver(self, frame: pb.AgentFrame) -> None:
        if self._queue is not None:
            with contextlib.suppress(Exception):
                self._queue.put_nowait(frame)

    async def run_turn(self, prompt, *, turn_id, client_callbacks, cancel_token) -> AsyncIterator[pb.AgentFrame]:
        async with self._turn_lock:
            try:
                acp = _import_acp()
                conn, sid = await self._supervisor.connection()
            except AgentUnavailable as e:
                yield agent_frame(turn_id, error=pb.ErrorOutput(
                    message=str(e), code="AGENT_UNAVAILABLE",
                    suggestion="set AGENT_BACKEND=null or install the 'agent' extra"))
                yield agent_frame(turn_id, turn_complete=pb.TurnComplete(status="error"))
                return
            except Exception as e:
                logger.warning("ACP agent spawn failed: %s", e)
                yield agent_frame(turn_id, error=pb.ErrorOutput(
                    message=f"agent unavailable: {e}", code="AGENT_SPAWN_ERROR",
                    suggestion="check AGENT_ACP_COMMAND and the model endpoint"))
                yield agent_frame(turn_id, turn_complete=pb.TurnComplete(status="error"))
                return

            self._queue = asyncio.Queue()
            self._cur_turn = turn_id
            prompt_task = asyncio.create_task(
                conn.prompt(prompt=[acp.text_block(prompt)], session_id=sid))
            prompt_done = False
            result_exc: "Exception | None" = None
            stop_reason = "end_turn"
            try:
                while True:
                    if cancel_token.cancelled:
                        with contextlib.suppress(Exception):
                            await conn.cancel(session_id=sid)
                        yield agent_frame(turn_id, turn_complete=pb.TurnComplete(status="cancelled"))
                        return
                    getter = asyncio.create_task(self._queue.get())
                    # Once the prompt completes, keep draining briefly: ACP delivers
                    # session/update notifications concurrently, so a chunk can still
                    # be in flight when the prompt response resolves.
                    wait_set = {getter} if prompt_done else {getter, prompt_task}
                    timeout = 0.1 if prompt_done else 0.5
                    done, _ = await asyncio.wait(
                        wait_set, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    if getter in done:
                        yield getter.result()
                        continue
                    getter.cancel()
                    if not prompt_done and prompt_task in done:
                        prompt_done = True
                        try:
                            resp = prompt_task.result()
                            stop_reason = getattr(resp, "stop_reason", "end_turn") or "end_turn"
                        except Exception as e:  # noqa: BLE001
                            result_exc = e
                        continue  # loop once more to drain stragglers
                    if prompt_done:
                        break  # queue idle for one cycle after the prompt → done
            finally:
                self._queue = None
                if not prompt_task.done():
                    prompt_task.cancel()
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await prompt_task
            if result_exc is not None:
                logger.warning("ACP prompt failed: %s", result_exc)
                yield agent_frame(turn_id, error=pb.ErrorOutput(
                    message=f"agent error: {result_exc}", code="AGENT_TURN_ERROR",
                    suggestion="the model endpoint may be unavailable"))
                yield agent_frame(turn_id, turn_complete=pb.TurnComplete(status="error"))
            else:
                yield agent_frame(turn_id, turn_complete=pb.TurnComplete(
                    status="ok", summary=f"({stop_reason})"))

    async def health_check(self) -> bool:
        try:
            return await self._supervisor.health_check()
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._supervisor.aclose()
