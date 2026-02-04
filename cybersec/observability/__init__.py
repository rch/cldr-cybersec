"""OpenTelemetry data processing and visualization module.

This module provides out-of-core processing and visualization of OpenTelemetry
data stored in Parquet format using Dask + HoloViews + PyArrow.

Designed to run independently of Apache Flink - use this for analytical queries
on historical data with a Dask cluster.

Example:
    from cybersec.observability import OTelDataset, OTelWriter
    from datetime import datetime, timedelta, timezone

    # Generate synthetic data for testing
    writer = OTelWriter("s3://cybersec/otel/", storage_options={...})
    writer.write_synthetic_spans(count=10_000)

    # Load and analyze
    dataset = OTelDataset("s3://cybersec/otel/", storage_options={...})
    ddf = dataset.load_spans(
        start_time=datetime.now(timezone.utc) - timedelta(hours=1),
        end_time=datetime.now(timezone.utc),
    )
    print(f"Loaded {len(ddf)} spans")

    # Visualize
    from cybersec.observability.viz import TraceVisualizer
    viz = TraceVisualizer(dataset)
    heatmap = viz.latency_heatmap(ddf)
"""

from cybersec.observability.schema import (
    LOGS_SCHEMA,
    METRICS_SCHEMA,
    SPANS_SCHEMA,
    SeverityNumber,
    SpanKind,
    StatusCode,
)

__all__ = [
    # Schema
    "SPANS_SCHEMA",
    "METRICS_SCHEMA",
    "LOGS_SCHEMA",
    "SpanKind",
    "StatusCode",
    "SeverityNumber",
    # Core classes (lazy loaded)
    "OTelDataset",
    "OTelWriter",
    # Factory functions (lazy loaded)
    "create_dataset_from_env",
    "create_writer_from_env",
]


# Lazy imports to avoid loading heavy dependencies until needed
def __getattr__(name: str):
    if name == "OTelDataset":
        from cybersec.observability.reader import OTelDataset
        return OTelDataset
    if name == "OTelWriter":
        from cybersec.observability.writer import OTelWriter
        return OTelWriter
    if name == "create_dataset_from_env":
        from cybersec.observability.reader import create_dataset_from_env
        return create_dataset_from_env
    if name == "create_writer_from_env":
        from cybersec.observability.writer import create_writer_from_env
        return create_writer_from_env
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
