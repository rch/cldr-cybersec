"""Restricted REPL — line-buffered command dispatch to gRPC engine.

NOT a bash shell.  Accumulates keystrokes, parses on Enter, dispatches
to the NavigatorEngine via gRPC.  Renders EngineEvents as ANSI terminal
output or JSON-RPC param_update frames.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb
from cybersec.pty_proxy.grpc_client import EngineClient

logger = logging.getLogger(__name__)

# ANSI escape helpers
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"

STYLE_MAP = {
    "success": _GREEN,
    "error": _RED,
    "warning": _YELLOW,
    "info": _CYAN,
    "dim": _DIM,
}

PROMPT = f"{_BOLD}{_CYAN}nav>{_RESET} "


class NavigatorREPL:
    """Restricted REPL that dispatches lines to the gRPC engine."""

    def __init__(self, engine: EngineClient, session_id: str = ""):
        self._engine = engine
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._line_buffer: list[str] = []

    async def feed(self, data: str) -> AsyncIterator[dict]:
        """Feed raw terminal input data and yield WebSocket frames.

        Yields dicts with either:
          {"type": "text", "data": "..."}           → write to terminal
          {"type": "param_update", "param": ..., "value": ..., "source": ...}
        """
        for ch in data:
            if ch == "\r" or ch == "\n":
                line = "".join(self._line_buffer).strip()
                self._line_buffer.clear()

                # Echo newline
                yield {"type": "text", "data": "\r\n"}

                if line:
                    async for frame in self._dispatch(line):
                        yield frame

                # Print fresh prompt
                yield {"type": "text", "data": PROMPT}

            elif ch == "\x7f" or ch == "\x08":
                # Backspace
                if self._line_buffer:
                    self._line_buffer.pop()
                    yield {"type": "text", "data": "\x08 \x08"}

            elif ch == "\x03":
                # Ctrl-C
                self._line_buffer.clear()
                yield {"type": "text", "data": "^C\r\n" + PROMPT}

            elif ch >= " " or ch == "\t":
                # Printable character
                self._line_buffer.append(ch)
                yield {"type": "text", "data": ch}

    async def _dispatch(self, line: str) -> AsyncIterator[dict]:
        """Parse line, call engine, yield frames."""
        command_id = f"{self._session_id}-{int(time.time()*1000)}"

        request = pb.CommandRequest(
            session_id=self._session_id,
            command_id=command_id,
            text=line,
        )

        try:
            async for event in self._engine.execute(request):
                async for frame in self._render_event(event):
                    yield frame
        except Exception as e:
            yield {"type": "text", "data": f"{_RED}Engine error: {e}{_RESET}\r\n"}

    async def _render_event(self, event: pb.EngineEvent) -> AsyncIterator[dict]:
        """Convert an EngineEvent to WebSocket frames."""
        which = event.WhichOneof("event")

        if which == "text_output":
            to = event.text_output
            color = STYLE_MAP.get(to.style, "")
            text = to.text.replace("\n", "\r\n")
            yield {"type": "text", "data": f"{color}{text}{_RESET}"}

        elif which == "progress":
            p = event.progress
            if p.fraction >= 0:
                bar_len = 30
                filled = int(bar_len * p.fraction)
                bar = "█" * filled + "░" * (bar_len - filled)
                yield {"type": "text", "data": f"\r{_DIM}[{bar}] {p.message}{_RESET}"}
            else:
                yield {"type": "text", "data": f"\r{_DIM}⠋ {p.message}{_RESET}"}

        elif which == "error":
            err = event.error
            yield {"type": "text", "data": f"{_RED}Error: {err.message}{_RESET}\r\n"}
            if err.suggestion:
                yield {"type": "text", "data": f"{_DIM}  Hint: {err.suggestion}{_RESET}\r\n"}

        elif which == "param_update":
            pu = event.param_update
            yield {
                "type": "param_update",
                "param": pu.param_name,
                "value": pu.value,
                "source": pu.source,
            }

        elif which == "status_snapshot":
            pass  # Already rendered as text_output by the engine

        elif which == "command_complete":
            cc = event.command_complete
            if cc.duration_ms > 0:
                yield {"type": "text", "data": f"{_DIM}({cc.duration_ms}ms){_RESET}\r\n"}

        elif which == "data_ready":
            dr = event.data_ready
            yield {"type": "text", "data": f"{_GREEN}Dataset ready: {dr.dataset_name} ({dr.partitions} partitions){_RESET}\r\n"}

        elif which == "data_result":
            pass  # Rendered via text_output by handler

    def get_banner(self) -> str:
        """Welcome banner for new connections."""
        return (
            f"\r\n{_BOLD}OTEL Navigator Engine{_RESET}\r\n"
            f"{_DIM}Type 'help' for commands, Ctrl-C to cancel{_RESET}\r\n\r\n"
            f"{PROMPT}"
        )
