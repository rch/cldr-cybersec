"""NavigatorEngine gRPC servicer implementation."""

from __future__ import annotations

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

    def __init__(self, dask_backend: DaskBackend | None = None):
        self._broadcast = BroadcastQueue()
        self._dask = dask_backend or DaskBackend()
        self._active_commands: dict[str, bool] = {}  # command_id → cancelled

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


def _event(command_id: str, **kwargs) -> pb.EngineEvent:
    return pb.EngineEvent(
        event_id=f"{command_id}-{int(time.time()*1000)}",
        command_id=command_id,
        timestamp_ms=int(time.time() * 1000),
        **kwargs,
    )
