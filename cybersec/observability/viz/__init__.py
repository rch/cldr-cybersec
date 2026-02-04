"""Visualization components for OpenTelemetry data.

This package provides HoloViews-based visualizations for traces, metrics,
and service topology using the Datashader rendering pipeline.

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.viz import TraceVisualizer, MetricsVisualizer

    dataset = OTelDataset("s3://cybersec/otel/")
    ddf = dataset.load_spans(...)

    # Trace visualizations
    trace_viz = TraceVisualizer(dataset)
    waterfall = trace_viz.waterfall(trace_id="abc123")
    heatmap = trace_viz.latency_heatmap(ddf)

    # Metrics visualizations
    metrics_viz = MetricsVisualizer(dataset)
    timeseries = metrics_viz.timeseries(metric_name="http_request_duration")
"""

__all__ = [
    "TraceVisualizer",
    "MetricsVisualizer",
    "TopologyVisualizer",
]


def __getattr__(name: str):
    """Lazy imports to avoid loading heavy visualization dependencies."""
    if name == "TraceVisualizer":
        from cybersec.observability.viz.traces import TraceVisualizer
        return TraceVisualizer
    if name == "MetricsVisualizer":
        from cybersec.observability.viz.metrics import MetricsVisualizer
        return MetricsVisualizer
    if name == "TopologyVisualizer":
        from cybersec.observability.viz.topology import TopologyVisualizer
        return TopologyVisualizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
