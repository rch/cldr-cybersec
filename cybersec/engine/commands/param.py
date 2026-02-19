"""SetParam command handler — drives Panel param changes via gRPC."""

from __future__ import annotations

import time
from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb

# Valid Panel params and their accepted values (mirrors SpanExplorer params)
VALID_PARAMS: dict[str, list[str] | None] = {
    "cmap": ["fire", "kbc", "bmy", "CET_L4", "rainbow4", "viridis", "inferno", "magma", "plasma"],
    "spread_enabled": ["true", "false"],
    "time_preset": ["Last 1 Hour", "Last 6 Hours", "Last 24 Hours", "Last 7 Days", "All Data"],
}


async def handle_set_param(
    request: pb.CommandRequest,
    command_id: str,
) -> AsyncIterator[pb.EngineEvent]:
    """Handle 'set <param> <value>' commands."""
    sp = request.set_param
    param_name = sp.param_name
    value = sp.value
    t0 = time.monotonic()

    # Validate param name
    if param_name not in VALID_PARAMS:
        yield _event(command_id, error=pb.ErrorOutput(
            message=f"Unknown param: {param_name}",
            code="INVALID_PARAM",
            suggestion=f"Valid params: {', '.join(sorted(VALID_PARAMS))}",
        ))
        yield _complete(command_id, "error", t0)
        return

    # Validate value if param has a fixed set
    allowed = VALID_PARAMS[param_name]
    if allowed is not None and value not in allowed:
        yield _event(command_id, error=pb.ErrorOutput(
            message=f"Invalid value '{value}' for {param_name}",
            code="INVALID_VALUE",
            suggestion=f"Valid values: {', '.join(allowed)}",
        ))
        yield _complete(command_id, "error", t0)
        return

    # Emit param update (Panel will apply this)
    yield _event(command_id, param_update=pb.ParamUpdate(
        param_name=param_name,
        value=value,
        source="engine",
    ))

    # Confirm in terminal
    yield _event(command_id, text_output=pb.TextOutput(
        text=f"{param_name} set to {value}\n",
        style="success",
    ))

    yield _complete(command_id, "ok", t0, summary=f"{param_name}={value}")


def _event(command_id: str, **kwargs) -> pb.EngineEvent:
    return pb.EngineEvent(
        event_id=f"{command_id}-{int(time.time()*1000)}",
        command_id=command_id,
        timestamp_ms=int(time.time() * 1000),
        **kwargs,
    )


def _complete(command_id: str, status: str, t0: float, summary: str = "") -> pb.EngineEvent:
    return _event(command_id, command_complete=pb.CommandComplete(
        status=status,
        duration_ms=int((time.monotonic() - t0) * 1000),
        summary=summary,
    ))
