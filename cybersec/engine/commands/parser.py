"""Parse raw REPL text into structured CommandRequest fields."""

from __future__ import annotations

import shlex

from cybersec.engine.generated import navigator_pb2 as pb


def parse_command(request: pb.CommandRequest) -> pb.CommandRequest:
    """Enrich a CommandRequest with structured fields parsed from ``text``.

    If a structured oneof is already set, return as-is.  Otherwise parse the
    raw ``text`` field into the appropriate structured command.
    """
    if request.WhichOneof("structured"):
        return request  # Already structured

    text = request.text.strip()
    if not text:
        return request

    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()

    verb = parts[0].lower() if parts else ""
    rest = parts[1:]

    enriched = pb.CommandRequest(
        session_id=request.session_id,
        command_id=request.command_id,
        text=request.text,
    )

    if verb == "set" and len(rest) >= 2:
        enriched.set_param.CopyFrom(
            pb.SetParamCommand(param_name=rest[0], value=" ".join(rest[1:]))
        )

    elif verb == "load" and rest:
        enriched.load_dataset.CopyFrom(
            pb.LoadDatasetCommand(dataset_name=rest[0], data_path=rest[1] if len(rest) > 1 else "")
        )

    elif verb == "query" and rest:
        # "query duration_ms > 500 last 1h" → expression=all, time_range from trailing "last Xh/Xm"
        expr_parts = []
        time_range = ""
        i = 0
        while i < len(rest):
            if rest[i].lower() == "last" and i + 1 < len(rest):
                time_range = f"last_{rest[i+1]}"
                i += 2
                continue
            expr_parts.append(rest[i])
            i += 1
        enriched.query.CopyFrom(
            pb.QueryCommand(expression=" ".join(expr_parts), time_range=time_range)
        )

    elif verb == "export" and rest:
        fmt = rest[0].lower()
        dest = rest[1] if len(rest) > 1 else ""
        enriched.export.CopyFrom(
            pb.ExportCommand(format=fmt, destination=dest)
        )

    # status, help, and unknown verbs remain as text-only
    return enriched
