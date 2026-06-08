"""Restricted REPL — line-buffered command dispatch to gRPC engine.

NOT a bash shell.  Accumulates keystrokes, parses on Enter, dispatches
to the NavigatorEngine via gRPC.  Renders EngineEvents as ANSI terminal
output or JSON-RPC param_update frames.
"""

from __future__ import annotations

import asyncio
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

    # Data-driven local command registry: canonical verb -> (method, help text).
    # Resolved BEFORE the line is forwarded to the engine, so these run client-side
    # in the pty-proxy and are deterministic — they work with NO model/agent
    # attached (the air-gap-without-model baseline). Unlike atelier's hardcoded
    # if-ladder, routing is table-driven so adding a command is one entry.
    LOCAL_COMMANDS = {
        "chk": ("_cmd_chk", "Local health sitrep — engine, Dask, dataset"),
        "help": ("_cmd_help", "Show commands (local + engine)"),
        "clear": ("_cmd_clear", "Clear the terminal screen"),
    }
    # Alias -> canonical verb. `health-check` mirrors atelier's chk alias.
    LOCAL_ALIASES = {"health-check": "chk", "health": "chk", "cls": "clear", "?": "help"}

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
        """Resolve the line against the local command registry first (client-side,
        no engine round-trip — works with no model in true air-gap); otherwise
        forward it to the gRPC engine."""
        verb = line.split()[0].lower() if line.strip() else ""
        canonical = self.LOCAL_ALIASES.get(verb, verb)
        entry = self.LOCAL_COMMANDS.get(canonical)
        if entry is not None:
            handler = getattr(self, entry[0])
            async for frame in handler(line):
                yield frame
            return
        async for frame in self._engine_dispatch(line):
            yield frame

    async def _engine_dispatch(self, line: str) -> AsyncIterator[dict]:
        """Send a line to the gRPC engine and render its streamed EngineEvents."""
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

    # ── Local (client-side) command handlers ───────────────────────────
    # The pure-local ones need no engine round-trip; `chk` makes a single
    # lightweight Status RPC. All are deterministic and work with no model
    # attached — this is the air-gap-without-model baseline.

    def _kv(self, label: str, value: str, ok: bool) -> dict:
        """Render one aligned status line with a colored pass/fail mark."""
        color = _GREEN if ok else _RED
        mark = "✓" if ok else "✗"
        return {"type": "text", "data": f"  {color}{mark}{_RESET} {label:<11}: {value}\r\n"}

    async def _cmd_chk(self, line: str) -> AsyncIterator[dict]:
        """Deterministic local health sitrep: engine reachability + Dask + dataset."""
        yield {"type": "text", "data": f"{_BOLD}nav health check{_RESET}\r\n"}
        try:
            st = await asyncio.wait_for(self._engine.status(), timeout=5.0)
            snap = st.snapshot
            yield self._kv("engine", f"OK (v{st.engine_version})", True)
            yield self._kv(
                "dask",
                f"connected, {snap.workers} worker(s)" if snap.dask_connected else "disconnected",
                snap.dask_connected,
            )
            ds = snap.current_dataset or "none"
            yield self._kv("dataset", f"{ds} ({snap.dataset_phase})", snap.dataset_phase == "ready")
            yield self._kv("processing", f"{snap.processing} task(s)", True)
        except asyncio.TimeoutError:
            yield self._kv("engine", "TIMEOUT (no response in 5s)", False)
            yield {"type": "text", "data": f"{_DIM}  hint: navigator-engine gRPC :50051 is slow or unreachable{_RESET}\r\n"}
        except Exception as e:
            yield self._kv("engine", f"UNREACHABLE: {e}", False)
            yield {"type": "text", "data": f"{_DIM}  hint: navigator-engine gRPC :50051 is not answering{_RESET}\r\n"}

    async def _cmd_clear(self, line: str) -> AsyncIterator[dict]:
        """Clear the terminal screen."""
        yield {"type": "text", "data": "\x1b[2J\x1b[H"}

    async def _cmd_help(self, line: str) -> AsyncIterator[dict]:
        """List local (baked-in) commands, then forward to the engine's own help."""
        yield {"type": "text", "data": f"{_BOLD}Local commands{_RESET} {_DIM}(client-side, no round-trip){_RESET}\r\n"}
        for verb, (_, desc) in self.LOCAL_COMMANDS.items():
            aliases = [a for a, c in self.LOCAL_ALIASES.items() if c == verb]
            alias_str = f" {_DIM}({', '.join(aliases)}){_RESET}" if aliases else ""
            yield {"type": "text", "data": f"  {_CYAN}{verb}{_RESET}{alias_str}  {_DIM}{desc}{_RESET}\r\n"}
        yield {"type": "text", "data": "\r\n"}
        async for frame in self._engine_dispatch("help"):
            yield frame

    def get_banner(self) -> str:
        """Welcome banner for new connections."""
        return (
            f"\r\n{_BOLD}OTEL Navigator Engine{_RESET}\r\n"
            f"{_DIM}Type 'help' for commands, 'chk' for health, Ctrl-C to cancel{_RESET}\r\n\r\n"
            f"{PROMPT}"
        )
