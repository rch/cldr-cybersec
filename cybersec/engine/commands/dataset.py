"""LoadDataset command handler — triggers data reload via engine."""

from __future__ import annotations

import time
from typing import AsyncIterator

from cybersec.engine.generated import navigator_pb2 as pb


async def handle_load_dataset(
    request: pb.CommandRequest,
    command_id: str,
    dask_backend,
) -> AsyncIterator[pb.EngineEvent]:
    """Handle 'load <dataset>' commands."""
    ld = request.load_dataset
    dataset_name = ld.dataset_name or "otel-minimal"
    t0 = time.monotonic()

    yield _event(command_id, text_output=pb.TextOutput(
        text=f"Loading dataset: {dataset_name}...\n",
        style="info",
    ))

    yield _event(command_id, progress=pb.ProgressUpdate(
        message=f"Resolving {dataset_name}",
        fraction=-1,  # indeterminate
    ))

    try:
        info = await dask_backend.load_dataset(dataset_name, data_path=ld.data_path or None)

        yield _event(command_id, data_ready=pb.DataReady(
            dataset_name=info.get("dataset", dataset_name),
            partitions=info.get("partitions", 0),
            total_rows_estimate=info.get("total_rows", 0),
            data_path=info.get("path", ""),
        ))

        yield _event(command_id, text_output=pb.TextOutput(
            text=f"Dataset ready: {info.get('partitions', '?')} partitions\n",
            style="success",
        ))

        yield _complete(command_id, "ok", t0, summary=f"loaded {dataset_name}")

    except Exception as e:
        yield _event(command_id, error=pb.ErrorOutput(
            message=str(e),
            code="DATASET_ERROR",
            suggestion="Check S3 connectivity and dataset path",
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
