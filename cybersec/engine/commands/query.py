"""Query command handler — filter/aggregate span data via Dask."""

from __future__ import annotations

import time
from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb


async def handle_query(
    request: pb.CommandRequest,
    command_id: str,
    dask_backend,
) -> AsyncIterator[pb.EngineEvent]:
    """Handle 'query <expression> [last Xh]' commands."""
    qc = request.query
    expression = qc.expression or request.text
    t0 = time.monotonic()

    yield _event(command_id, text_output=pb.TextOutput(
        text=f"Querying: {expression}\n",
        style="info",
    ))

    yield _event(command_id, progress=pb.ProgressUpdate(
        message="Scanning partitions...",
        fraction=-1,
    ))

    try:
        result = await dask_backend.query(
            expression=expression,
            time_range=qc.time_range,
            limit=qc.limit or 100,
        )

        row_count = result.get("row_count", 0)
        summary_text = result.get("summary", f"Found {row_count:,} matching spans")

        yield _event(command_id, text_output=pb.TextOutput(
            text=f"{summary_text}\n",
            style="success",
        ))

        if result.get("table_text"):
            yield _event(command_id, text_output=pb.TextOutput(
                text=result["table_text"] + "\n",
                style="dim",
            ))

        yield _event(command_id, data_result=pb.DataResult(
            format="table",
            row_count=row_count,
        ))

        yield _complete(command_id, "ok", t0, summary=f"{row_count:,} spans")

    except Exception as e:
        yield _event(command_id, error=pb.ErrorOutput(
            message=str(e),
            code="QUERY_ERROR",
            suggestion="Check expression syntax. Example: query duration_ms > 500",
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
