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

    # Hierarchical drill-down (zoom-triggered aggregation)
    from cybersec.observability.viz import create_temporal_drilldown
    drilldown = create_temporal_drilldown(ddf)
"""

__all__ = [
    "TraceVisualizer",
    "MetricsVisualizer",
    "TopologyVisualizer",
    "DrilldownHeatmap",
    "OverviewDetailView",
    "ServiceLatencyExplorer",
    "create_temporal_drilldown",
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
    if name == "DrilldownHeatmap":
        from cybersec.observability.viz.drilldown import DrilldownHeatmap
        return DrilldownHeatmap
    if name == "OverviewDetailView":
        from cybersec.observability.viz.drilldown import OverviewDetailView
        return OverviewDetailView
    if name == "ServiceLatencyExplorer":
        from cybersec.observability.viz.drilldown import ServiceLatencyExplorer
        return ServiceLatencyExplorer
    if name == "create_temporal_drilldown":
        from cybersec.observability.viz.drilldown import create_temporal_drilldown
        return create_temporal_drilldown
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
