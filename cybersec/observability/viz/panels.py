"""Interactive Panel widgets for OTel data exploration.

This module provides Panel-based interactive widgets for:
- Time range selection
- Service filtering
- Trace search
- Linked views and dashboards

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.viz.panels import OTelExplorer

    dataset = OTelDataset("s3://cybersec/otel/")
    explorer = OTelExplorer(dataset)
    explorer.show()  # Opens in browser or notebook
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

import holoviews as hv
import panel as pn
import param

if TYPE_CHECKING:
    import dask.dataframe as dd
    import pandas as pd
    from cybersec.observability.reader import OTelDataset

logger = logging.getLogger(__name__)

# Enable extensions
hv.extension("bokeh")
pn.extension()


class TimeRangeSelector(param.Parameterized):
    """Widget for selecting time ranges."""

    preset = param.Selector(
        default="Last Hour",
        objects=["Last 15 Minutes", "Last Hour", "Last 6 Hours", "Last 24 Hours", "Last 7 Days", "Custom"],
        doc="Preset time range",
    )

    start_time = param.Date(
        default=datetime.now(timezone.utc) - timedelta(hours=1),
        doc="Start of time range",
    )

    end_time = param.Date(
        default=datetime.now(timezone.utc),
        doc="End of time range",
    )

    def __init__(self, **params):
        super().__init__(**params)
        self._update_from_preset()

    @param.depends("preset", watch=True)
    def _update_from_preset(self) -> None:
        """Update start/end times based on preset selection."""
        now = datetime.now(timezone.utc)

        presets = {
            "Last 15 Minutes": timedelta(minutes=15),
            "Last Hour": timedelta(hours=1),
            "Last 6 Hours": timedelta(hours=6),
            "Last 24 Hours": timedelta(hours=24),
            "Last 7 Days": timedelta(days=7),
        }

        if self.preset in presets:
            self.end_time = now
            self.start_time = now - presets[self.preset]

    @property
    def time_range(self) -> tuple[datetime, datetime]:
        """Get the current time range as a tuple."""
        return (self.start_time, self.end_time)

    def panel(self) -> pn.Column:
        """Create Panel widget for time range selection."""
        preset_select = pn.widgets.Select.from_param(
            self.param.preset,
            name="Time Range",
        )

        start_picker = pn.widgets.DatetimePicker.from_param(
            self.param.start_time,
            name="Start Time",
            disabled=self.preset != "Custom",
        )

        end_picker = pn.widgets.DatetimePicker.from_param(
            self.param.end_time,
            name="End Time",
            disabled=self.preset != "Custom",
        )

        # Enable/disable date pickers based on preset
        @pn.depends(self.param.preset, watch=True)
        def update_disabled(preset: str) -> None:
            is_custom = preset == "Custom"
            start_picker.disabled = not is_custom
            end_picker.disabled = not is_custom

        return pn.Column(
            preset_select,
            start_picker,
            end_picker,
            name="Time Range",
        )


class ServiceFilter(param.Parameterized):
    """Widget for filtering by service names."""

    services = param.ListSelector(
        default=[],
        objects=[],
        doc="Selected services",
    )

    def __init__(self, available_services: list[str] | None = None, **params):
        super().__init__(**params)
        if available_services:
            self.param.services.objects = available_services

    def update_services(self, services: list[str]) -> None:
        """Update available services."""
        self.param.services.objects = services

    def panel(self) -> pn.Column:
        """Create Panel widget for service selection."""
        return pn.Column(
            pn.widgets.MultiSelect.from_param(
                self.param.services,
                name="Services",
                size=8,
            ),
            name="Service Filter",
        )


class TraceSearch(param.Parameterized):
    """Widget for searching traces by ID."""

    trace_id = param.String(
        default="",
        doc="Trace ID to search for",
    )

    search_button = param.Action(
        lambda self: self._trigger_search(),
        doc="Trigger search",
    )

    def __init__(self, on_search: Callable[[str], None] | None = None, **params):
        super().__init__(**params)
        self._on_search = on_search

    def _trigger_search(self) -> None:
        """Trigger the search callback."""
        if self._on_search and self.trace_id:
            self._on_search(self.trace_id)

    def panel(self) -> pn.Column:
        """Create Panel widget for trace search."""
        return pn.Column(
            pn.widgets.TextInput.from_param(
                self.param.trace_id,
                name="Trace ID",
                placeholder="Enter trace ID...",
            ),
            pn.widgets.Button.from_param(
                self.param.search_button,
                name="Search",
                button_type="primary",
            ),
            name="Trace Search",
        )


class OTelExplorer(param.Parameterized):
    """Main interactive explorer for OTel data.

    Combines time range selection, service filtering, and visualizations
    into an integrated dashboard.
    """

    # View mode
    view_mode = param.Selector(
        default="Overview",
        objects=["Overview", "Traces", "Metrics", "Topology"],
        doc="Current view mode",
    )

    # Refresh trigger
    refresh = param.Action(
        lambda self: self._refresh_data(),
        doc="Refresh data",
    )

    def __init__(
        self,
        dataset: OTelDataset,
        **params,
    ) -> None:
        """Initialize OTelExplorer.

        Args:
            dataset: OTelDataset for loading data
        """
        super().__init__(**params)
        self.dataset = dataset

        # Initialize sub-widgets
        self.time_range = TimeRangeSelector()
        self.service_filter = ServiceFilter()
        self.trace_search = TraceSearch(on_search=self._on_trace_search)

        # Data cache
        self._spans_cache: pd.DataFrame | None = None
        self._metrics_cache: pd.DataFrame | None = None

        # Selected trace for detail view
        self._selected_trace_id: str | None = None

    def _refresh_data(self) -> None:
        """Refresh data from dataset."""
        self._spans_cache = None
        self._metrics_cache = None
        self._update_services()

    def _update_services(self) -> None:
        """Update available services from current data."""
        try:
            start_time, end_time = self.time_range.time_range
            services = self.dataset.list_services(start_time, end_time)
            self.service_filter.update_services(services)
        except Exception as e:
            logger.warning(f"Failed to update services: {e}")

    def _load_spans(self) -> pd.DataFrame:
        """Load spans with current filters."""
        if self._spans_cache is not None:
            return self._spans_cache

        start_time, end_time = self.time_range.time_range
        service_names = self.service_filter.services or None

        ddf = self.dataset.load_spans(
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
        )
        self._spans_cache = ddf.compute()
        return self._spans_cache

    def _load_metrics(self) -> pd.DataFrame:
        """Load metrics with current filters."""
        if self._metrics_cache is not None:
            return self._metrics_cache

        start_time, end_time = self.time_range.time_range
        service_names = self.service_filter.services or None

        ddf = self.dataset.load_metrics(
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
        )
        self._metrics_cache = ddf.compute()
        return self._metrics_cache

    def _on_trace_search(self, trace_id: str) -> None:
        """Handle trace search."""
        self._selected_trace_id = trace_id
        self.view_mode = "Traces"

    @param.depends("view_mode", "time_range.preset", "service_filter.services")
    def main_view(self) -> pn.viewable.Viewable:
        """Generate the main view based on current mode and filters."""
        try:
            if self.view_mode == "Overview":
                return self._overview_view()
            elif self.view_mode == "Traces":
                return self._traces_view()
            elif self.view_mode == "Metrics":
                return self._metrics_view()
            elif self.view_mode == "Topology":
                return self._topology_view()
            else:
                return pn.pane.Markdown("## Unknown View")
        except Exception as e:
            logger.error(f"Error generating view: {e}")
            return pn.pane.Alert(f"Error: {e}", alert_type="danger")

    def _overview_view(self) -> pn.viewable.Viewable:
        """Generate overview dashboard."""
        from cybersec.observability.viz.traces import TraceVisualizer
        from cybersec.observability.viz.topology import TopologyVisualizer

        df = self._load_spans()

        if len(df) == 0:
            return pn.pane.Markdown("## No data in selected time range")

        trace_viz = TraceVisualizer(self.dataset)
        topo_viz = TopologyVisualizer(self.dataset)

        # Create overview components
        span_count_chart = trace_viz.span_count_timeseries(df, width=600, height=250)
        error_timeline = trace_viz.error_timeline(df, width=600, height=200)
        service_graph = topo_viz.service_graph(df, width=500, height=400)

        # Summary stats
        stats = self._compute_stats(df)
        stats_pane = pn.pane.Markdown(f"""
## Overview

| Metric | Value |
|--------|-------|
| Total Spans | {stats['span_count']:,} |
| Unique Traces | {stats['trace_count']:,} |
| Services | {stats['service_count']} |
| Error Rate | {stats['error_rate']:.2%} |
| Avg Latency | {stats['avg_latency_ms']:.2f} ms |
| P99 Latency | {stats['p99_latency_ms']:.2f} ms |
""")

        return pn.Column(
            stats_pane,
            pn.Row(
                pn.Column(span_count_chart, error_timeline),
                service_graph,
            ),
        )

    def _traces_view(self) -> pn.viewable.Viewable:
        """Generate traces view."""
        from cybersec.observability.viz.traces import TraceVisualizer

        df = self._load_spans()

        if len(df) == 0:
            return pn.pane.Markdown("## No traces in selected time range")

        trace_viz = TraceVisualizer(self.dataset)

        # If a specific trace is selected, show waterfall
        if self._selected_trace_id:
            waterfall = trace_viz.waterfall(trace_id=self._selected_trace_id)
            return pn.Column(
                pn.pane.Markdown(f"## Trace: {self._selected_trace_id}"),
                waterfall,
            )

        # Otherwise show latency heatmap and distribution
        heatmap = trace_viz.latency_heatmap(df, width=800, height=300)
        distribution = trace_viz.latency_distribution(df, width=700, height=300)
        flame = trace_viz.flame_graph(df, width=800, height=400)

        return pn.Column(
            pn.pane.Markdown("## Trace Analysis"),
            heatmap,
            pn.Row(distribution, flame),
        )

    def _metrics_view(self) -> pn.viewable.Viewable:
        """Generate metrics view."""
        from cybersec.observability.viz.metrics import MetricsVisualizer

        df = self._load_metrics()

        if len(df) == 0:
            return pn.pane.Markdown("## No metrics in selected time range")

        metrics_viz = MetricsVisualizer(self.dataset)

        # Get unique metric names
        metric_names = df["metric_name"].unique().tolist()[:6]

        if not metric_names:
            return pn.pane.Markdown("## No metrics found")

        # Create grid of metric time series
        start_time, end_time = self.time_range.time_range
        grid = metrics_viz.multi_metric_grid(
            metric_names=metric_names,
            start_time=start_time,
            end_time=end_time,
            cols=2,
            width=400,
            height=250,
        )

        return pn.Column(
            pn.pane.Markdown("## Metrics"),
            grid,
        )

    def _topology_view(self) -> pn.viewable.Viewable:
        """Generate topology view."""
        from cybersec.observability.viz.topology import TopologyVisualizer

        df = self._load_spans()

        if len(df) == 0:
            return pn.pane.Markdown("## No topology data in selected time range")

        topo_viz = TopologyVisualizer(self.dataset)

        graph = topo_viz.service_graph(df, width=800, height=500)
        matrix = topo_viz.dependency_matrix(df, width=500, height=500)
        health = topo_viz.service_health_dashboard(
            start_time=self.time_range.start_time,
            end_time=self.time_range.end_time,
        )

        return pn.Column(
            pn.pane.Markdown("## Service Topology"),
            pn.Row(graph, matrix),
            pn.pane.Markdown("## Service Health"),
            health,
        )

    def _compute_stats(self, df: pd.DataFrame) -> dict[str, Any]:
        """Compute summary statistics."""
        return {
            "span_count": len(df),
            "trace_count": df["trace_id"].nunique(),
            "service_count": df["service_name"].nunique(),
            "error_rate": (df["status_code"] == "ERROR").mean(),
            "avg_latency_ms": df["duration_ns"].mean() / 1_000_000,
            "p99_latency_ms": df["duration_ns"].quantile(0.99) / 1_000_000,
        }

    def sidebar(self) -> pn.Column:
        """Generate sidebar with controls."""
        return pn.Column(
            pn.pane.Markdown("# OTel Explorer"),
            pn.widgets.Select.from_param(
                self.param.view_mode,
                name="View",
            ),
            pn.widgets.Button.from_param(
                self.param.refresh,
                name="Refresh",
                button_type="default",
            ),
            pn.layout.Divider(),
            self.time_range.panel(),
            pn.layout.Divider(),
            self.service_filter.panel(),
            pn.layout.Divider(),
            self.trace_search.panel(),
            width=250,
        )

    def servable(self) -> pn.Template:
        """Create servable Panel application."""
        template = pn.template.FastListTemplate(
            title="OTel Telemetry Explorer",
            sidebar=[self.sidebar()],
            main=[self.main_view],
            accent_base_color="#1f77b4",
            header_background="#1f77b4",
        )
        return template

    def show(self, **kwargs) -> None:
        """Show the explorer in a browser or notebook."""
        self.servable().show(**kwargs)

    def notebook(self) -> pn.Row:
        """Return a notebook-friendly layout."""
        return pn.Row(
            self.sidebar(),
            pn.Column(self.main_view, sizing_mode="stretch_width"),
        )


def create_explorer_from_env() -> OTelExplorer:
    """Create OTelExplorer with configuration from environment.

    Returns:
        Configured OTelExplorer instance
    """
    from cybersec.observability.reader import create_dataset_from_env

    dataset = create_dataset_from_env()
    return OTelExplorer(dataset)
