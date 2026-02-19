"""Export command handler — export filtered data to CSV/Parquet/JSON."""

from __future__ import annotations

import time
from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb

VALID_FORMATS = {"csv", "parquet", "json"}


async def handle_export(
    request: pb.CommandRequest,
    command_id: str,
    dask_backend,
) -> AsyncIterator[pb.EngineEvent]:
    """Handle 'export <format> [destination]' commands."""
    ec = request.export
    fmt = ec.format.lower()
    t0 = time.monotonic()

    if fmt not in VALID_FORMATS:
        yield _event(command_id, error=pb.ErrorOutput(
            message=f"Unknown format: {fmt}",
            code="INVALID_FORMAT",
            suggestion=f"Valid formats: {', '.join(sorted(VALID_FORMATS))}",
        ))
        yield _complete(command_id, "error", t0)
        return

    yield _event(command_id, text_output=pb.TextOutput(
        text=f"Exporting as {fmt}...\n",
        style="info",
    ))

    try:
        result = await dask_backend.export_data(
            format=fmt,
            destination=ec.destination or None,
            expression=ec.expression or None,
        )

        url = result.get("url", "")
        row_count = result.get("row_count", 0)

        yield _event(command_id, data_result=pb.DataResult(
            format=fmt,
            url=url,
            row_count=row_count,
        ))

        yield _event(command_id, text_output=pb.TextOutput(
            text=f"Exported {row_count:,} rows → {url or 'inline'}\n",
            style="success",
        ))

        yield _complete(command_id, "ok", t0, summary=f"exported {row_count:,} rows")

    except Exception as e:
        yield _event(command_id, error=pb.ErrorOutput(
            message=str(e),
            code="EXPORT_ERROR",
        ))
        yield _complete(command_id, "error", t0)


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
