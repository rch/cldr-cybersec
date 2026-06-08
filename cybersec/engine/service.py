"""NavigatorEngine gRPC servicer implementation."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import grpc

from cybersec.engine.generated import navigator_pb2 as pb
from cybersec.engine.generated import navigator_pb2_grpc
from cybersec.engine.broadcast import BroadcastQueue
from cybersec.engine.dask_backend import DaskBackend
from cybersec.engine.commands.parser import parse_command
from cybersec.engine.commands.param import handle_set_param
from cybersec.engine.commands.dataset import handle_load_dataset
from cybersec.engine.commands.query import handle_query
from cybersec.engine.commands.export import handle_export
from cybersec.engine.agent import (
    make_agent_session,
    AgentSession,
    CancelToken,
    ClientCallbacks,
    agent_frame,
)

logger = logging.getLogger(__name__)

ENGINE_VERSION = "0.1.0"

# Commands handled as plain text (no structured oneof)
HELP_TEXT = """\
\x1b[1mNavigator Engine Commands\x1b[0m

  \x1b[36mstatus\x1b[0m                         Show Dask cluster & dataset status
  \x1b[36mset\x1b[0m <param> <value>             Set a visualization param
      params: cmap, spread_enabled, time_preset
  \x1b[36mload\x1b[0m <dataset>                  Load a dataset (e.g. otel-minimal, otel-1t)
  \x1b[36mquery\x1b[0m <expression> [last Xh]    Filter spans (pandas query syntax)
  \x1b[36mexport\x1b[0m <csv|parquet|json> [dst]  Export data to S3
  \x1b[36mhelp\x1b[0m                            Show this help

Examples:
  set cmap viridis
  load otel-1t
  query duration_ms > 500 last 1h
  export csv
"""


class NavigatorEngineServicer(navigator_pb2_grpc.NavigatorEngineServicer):
    """gRPC servicer that dispatches commands to handlers."""

    def __init__(self, dask_backend: DaskBackend | None = None,
                 agent: "AgentSession | None" = None):
        self._broadcast = BroadcastQueue()
        self._dask = dask_backend or DaskBackend()
        self._active_commands: dict[str, bool] = {}  # command_id → cancelled
        # OPTIONAL agent backend. Defaults to NullAgentSession (off) — the
        # deterministic REPL is unaffected and imports no ACP code.
        self._agent = agent if agent is not None else make_agent_session()

    async def Execute(self, request: pb.CommandRequest, context: grpc.aio.ServicerContext):
        """Execute a command and stream EngineEvents back."""
        command_id = request.command_id or str(uuid.uuid4())[:8]
        self._active_commands[command_id] = False

        try:
            # Parse text into structured command if needed
            request = parse_command(request)
            structured = request.WhichOneof("structured")
            verb = request.text.strip().split()[0].lower() if request.text.strip() else ""

            if structured == "set_param":
                handler = handle_set_param(request, command_id)
            elif structured == "load_dataset":
                handler = handle_load_dataset(request, command_id, self._dask)
            elif structured == "query":
                handler = handle_query(request, command_id, self._dask)
            elif structured == "export":
                handler = handle_export(request, command_id, self._dask)
            elif verb == "status":
                handler = self._handle_status(command_id)
            elif verb == "help":
                handler = self._handle_help(command_id)
            elif verb in ("ask", "/ai"):
                handler = self._handle_agent(command_id, _strip_prompt(request.text))
            else:
                handler = self._handle_unknown(command_id, request.text)

            async for event in handler:
                if self._active_commands.get(command_id):
                    # Cancelled
                    yield _event(command_id, command_complete=pb.CommandComplete(
                        status="cancelled",
                    ))
                    break
                self._broadcast.publish(event)
                yield event

        finally:
            self._active_commands.pop(command_id, None)

    async def Cancel(self, request: pb.CancelRequest, context: grpc.aio.ServicerContext):
        """Cancel an in-flight Execute stream."""
        if request.command_id in self._active_commands:
            self._active_commands[request.command_id] = True
            logger.info("Cancelled command %s", request.command_id)
        from google.protobuf.empty_pb2 import Empty
        return Empty()

    async def Status(self, request: pb.StatusRequest, context: grpc.aio.ServicerContext):
        """Get engine status."""
        stats = await self._dask.status()
        from google.protobuf.timestamp_pb2 import Timestamp
        now = Timestamp()
        now.GetCurrentTime()
        return pb.StatusResponse(
            snapshot=pb.StatusSnapshot(
                workers=stats["workers"],
                processing=stats["processing"],
                dask_connected=stats["dask_connected"],
                current_dataset=stats["current_dataset"],
                dataset_phase=stats["dataset_phase"],
                partitions=stats["partitions"],
            ),
            server_time=now,
            engine_version=ENGINE_VERSION,
        )

    async def Subscribe(self, request: pb.SubscribeRequest, context: grpc.aio.ServicerContext):
        """Subscribe to engine-initiated events."""
        queue = self._broadcast.subscribe()
        logger.info("New subscriber: session=%s (replay=%s)", request.session_id, request.replay_history)

        # Replay history if requested
        if request.replay_history:
            limit = request.replay_limit or 50
            for event in self._broadcast.get_history(limit=limit):
                yield event

        # Stream live events until client disconnects
        while not context.cancelled():
            try:
                event = await queue.get()
                yield event
            except Exception:
                break

    async def AgentSession(self, request_iterator, context: grpc.aio.ServicerContext):
        """Federated ACP agent session (bidirectional). Reads the opening prompt
        ClientFrame, runs the agent turn, and streams AgentFrames. Subsequent
        ClientFrames (cancel / permission_response / fs_response) are drained by a
        background task. With the default null backend this yields a single
        turn_complete{status="disabled"} and returns — the deterministic REPL is
        never affected."""
        session_id = ""
        turn_id = ""
        prompt_text = None
        async for cf in request_iterator:
            session_id = cf.session_id or session_id
            turn_id = cf.turn_id or turn_id
            if cf.WhichOneof("frame") == "prompt":
                prompt_text = cf.prompt.text
                break
        if prompt_text is None:
            return  # client closed without sending a prompt

        tid = turn_id or str(uuid.uuid4())[:8]
        self._active_commands[tid] = False
        cancel = CancelToken(is_cancelled=lambda: self._active_commands.get(tid, False))
        callbacks = ClientCallbacks()  # v1: engine-side policy (P3); null ignores

        async def _drain():
            try:
                async for cf in request_iterator:
                    if cf.WhichOneof("frame") == "cancel":
                        self._active_commands[tid] = True
                    # permission_response / fs_response wire to callbacks in P3
            except Exception:
                pass

        drain_task = asyncio.create_task(_drain())
        try:
            async for af in self._agent.run_turn(
                prompt_text, turn_id=tid, client_callbacks=callbacks, cancel_token=cancel,
            ):
                af.session_id = session_id
                yield af
        except Exception as e:
            logger.warning("AgentSession turn failed: %s", e)
            yield agent_frame(tid, error=pb.ErrorOutput(
                message=f"agent error: {e}", code="AGENT_ERROR",
                suggestion="the model endpoint may be unavailable"))
            yield agent_frame(tid, turn_complete=pb.TurnComplete(status="error"))
        finally:
            drain_task.cancel()
            self._active_commands.pop(tid, None)

    async def aclose(self) -> None:
        """Release the agent backend (subprocess, channels). Idempotent."""
        try:
            await self._agent.aclose()
        except Exception:
            logger.debug("agent aclose error", exc_info=True)

    # ── Built-in command handlers ──────────────────────────────────────

    async def _handle_status(self, command_id: str):
        t0 = time.monotonic()
        stats = await self._dask.status()

        snapshot = pb.StatusSnapshot(
            workers=stats["workers"],
            processing=stats["processing"],
            dask_connected=stats["dask_connected"],
            current_dataset=stats["current_dataset"],
            dataset_phase=stats["dataset_phase"],
            partitions=stats["partitions"],
        )
        yield _event(command_id, status_snapshot=snapshot)

        # Also render as terminal text
        connected = "\x1b[32mconnected\x1b[0m" if stats["dask_connected"] else "\x1b[31mdisconnected\x1b[0m"
        text = (
            f"Dask: {connected}  workers={stats['workers']}  processing={stats['processing']}\n"
            f"Dataset: {stats['current_dataset']}  phase={stats['dataset_phase']}  partitions={stats['partitions']}\n"
        )
        yield _event(command_id, text_output=pb.TextOutput(text=text, style="info"))
        yield _event(command_id, command_complete=pb.CommandComplete(
            status="ok",
            duration_ms=int((time.monotonic() - t0) * 1000),
        ))

    async def _handle_help(self, command_id: str):
        yield _event(command_id, text_output=pb.TextOutput(text=HELP_TEXT, style="info"))
        yield _event(command_id, command_complete=pb.CommandComplete(status="ok"))

    async def _handle_unknown(self, command_id: str, text: str):
        yield _event(command_id, error=pb.ErrorOutput(
            message=f"Unknown command: {text.strip().split()[0] if text.strip() else '(empty)'}",
            code="UNKNOWN_COMMAND",
            suggestion="Type 'help' for available commands",
        ))
        yield _event(command_id, command_complete=pb.CommandComplete(status="error"))

    async def _handle_agent(self, command_id: str, prompt: str):
        """Run the OPTIONAL agent and adapt its AgentFrames to EngineEvents so it
        streams over the EXISTING Execute server-stream (zero client change). With
        the default null backend this prints the friendly 'agent disabled' line."""
        prompt = (prompt or "").strip()
        if not prompt:
            yield _event(command_id, error=pb.ErrorOutput(
                message="usage: ask <question>", code="AGENT_USAGE",
                suggestion="e.g. ask which spans had errors in the last hour"))
            yield _event(command_id, command_complete=pb.CommandComplete(status="error"))
            return
        cancel = CancelToken(is_cancelled=lambda: self._active_commands.get(command_id, False))
        callbacks = ClientCallbacks()
        try:
            async for af in self._agent.run_turn(
                prompt, turn_id=command_id, client_callbacks=callbacks, cancel_token=cancel,
            ):
                for ev in _agentframe_to_events(af, command_id):
                    yield ev
        except Exception as e:
            logger.warning("agent turn failed: %s", e)
            yield _event(command_id, error=pb.ErrorOutput(
                message=f"agent error: {e}", code="AGENT_ERROR",
                suggestion="the model endpoint may be unavailable"))
            yield _event(command_id, command_complete=pb.CommandComplete(status="error"))


def _event(command_id: str, **kwargs) -> pb.EngineEvent:
    return pb.EngineEvent(
        event_id=f"{command_id}-{int(time.time()*1000)}",
        command_id=command_id,
        timestamp_ms=int(time.time() * 1000),
        **kwargs,
    )


def _strip_prompt(text: str) -> str:
    """Drop the leading verb (``ask`` / ``/ai``) and return the remaining prompt."""
    parts = text.strip().split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _agentframe_to_events(af: pb.AgentFrame, command_id: str) -> list:
    """Adapt an AgentFrame to EngineEvent(s) for the Execute-adapter path, so the
    agent streams over the existing server-stream before the native AgentSession
    RPC client exists. Maps onto the EngineEvent oneof the REPL already renders."""
    which = af.WhichOneof("frame")
    out = []
    if which == "assistant_chunk":
        ac = af.assistant_chunk
        out.append(_event(command_id, text_output=pb.TextOutput(
            text=ac.text, style="dim" if ac.thinking else "")))
    elif which == "tool_call":
        out.append(_event(command_id, text_output=pb.TextOutput(
            text=f"⚙ {af.tool_call.title}\n", style="dim")))
    elif which == "tool_call_update":
        if af.tool_call_update.content_delta:
            out.append(_event(command_id, text_output=pb.TextOutput(
                text=af.tool_call_update.content_delta, style="dim")))
    elif which == "error":
        out.append(_event(command_id, error=af.error))
    elif which in ("permission_request", "fs_request"):
        title = (af.permission_request.title if which == "permission_request"
                 else af.fs_request.path)
        out.append(_event(command_id, text_output=pb.TextOutput(text=f"… {title}\n", style="dim")))
    elif which == "turn_complete":
        tc = af.turn_complete
        if tc.summary:
            style = "info" if tc.status in ("ok", "disabled") else "warning"
            out.append(_event(command_id, text_output=pb.TextOutput(text=tc.summary + "\n", style=style)))
        out.append(_event(command_id, command_complete=pb.CommandComplete(
            status=("ok" if tc.status == "disabled" else tc.status),
            duration_ms=tc.duration_ms)))
    return out
