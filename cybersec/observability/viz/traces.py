"""Trace visualization components using HoloViews.

This module provides trace-specific visualizations:
- Waterfall/Gantt charts for individual traces
- Flame graphs for aggregated span analysis
- Latency heatmaps for time-based analysis

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.viz.traces import TraceVisualizer

    dataset = OTelDataset("s3://cybersec/otel/")
    viz = TraceVisualizer(dataset)

    # Single trace waterfall
    waterfall = viz.waterfall(trace_id="abc123")

    # Latency heatmap across time
    ddf = dataset.load_spans(...)
    heatmap = viz.latency_heatmap(ddf)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import holoviews as hv
import numpy as np
import pandas as pd
from holoviews import opts

try:
    import datashader as ds
    from holoviews.operation.datashader import rasterize, shade

    HAS_DATASHADER = True
except ImportError:
    HAS_DATASHADER = False

if TYPE_CHECKING:
    import dask.dataframe as dd
    from cybersec.observability.reader import OTelDataset

logger = logging.getLogger(__name__)

# Enable Bokeh extension
hv.extension("bokeh")


# Color schemes
STATUS_COLORS = {
    "OK": "#4CAF50",      # Green
    "ERROR": "#F44336",   # Red
    "UNSET": "#9E9E9E",   # Gray
}

SERVICE_COLORS = [
    "#1f77b4",  # Blue
    "#ff7f0e",  # Orange
    "#2ca02c",  # Green
    "#d62728",  # Red
    "#9467bd",  # Purple
    "#8c564b",  # Brown
    "#e377c2",  # Pink
    "#7f7f7f",  # Gray
    "#bcbd22",  # Olive
    "#17becf",  # Cyan
]


class TraceVisualizer:
    """HoloViews-based trace visualizations.

    Provides visualization methods for OpenTelemetry trace data including
    waterfall diagrams, flame graphs, and latency heatmaps.

    Attributes:
        dataset: OTelDataset instance for loading data
    """

    def __init__(self, dataset: OTelDataset | None = None) -> None:
        """Initialize TraceVisualizer.

        Args:
            dataset: Optional OTelDataset for loading trace data.
                     If not provided, visualizations must receive data directly.
        """
        self.dataset = dataset
        self._service_color_map: dict[str, str] = {}

    def _get_service_color(self, service: str) -> str:
        """Get consistent color for a service."""
        if service not in self._service_color_map:
            idx = len(self._service_color_map) % len(SERVICE_COLORS)
            self._service_color_map[service] = SERVICE_COLORS[idx]
        return self._service_color_map[service]

    def waterfall(
        self,
        trace_id: str | None = None,
        df: pd.DataFrame | None = None,
        width: int = 900,
        height: int = 400,
        show_labels: bool = True,
    ) -> hv.Layout:
        """Render a trace as a waterfall/Gantt chart.

        Shows spans as horizontal bars with timing aligned to trace start.

        Args:
            trace_id: Trace ID to load (requires dataset)
            df: Pre-loaded DataFrame with trace spans
            width: Chart width in pixels
            height: Chart height in pixels
            show_labels: Whether to show span name labels

        Returns:
            HoloViews Layout with waterfall chart
        """
        if df is None:
            if self.dataset is None or trace_id is None:
                raise ValueError("Must provide either df or trace_id with dataset")
            result = self.dataset.get_trace(trace_id)
            df = result["spans"].compute()

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No spans found for trace").opts(
                width=width, height=height
            )

        # Sort by start time
        df = df.sort_values("start_time_unix_nano")

        # Calculate relative times from trace start
        trace_start = df["start_time_unix_nano"].min()
        df = df.copy()
        df["rel_start_ms"] = (df["start_time_unix_nano"] - trace_start) / 1_000_000
        df["rel_end_ms"] = (df["end_time_unix_nano"] - trace_start) / 1_000_000
        df["duration_ms"] = df["duration_ns"] / 1_000_000

        # Assign y-positions based on hierarchy
        df["y_pos"] = self._compute_y_positions(df)

        # Create segments for each span
        segments_data = []
        for _, row in df.iterrows():
            color = STATUS_COLORS.get(row["status_code"], STATUS_COLORS["UNSET"])
            segments_data.append({
                "x0": row["rel_start_ms"],
                "x1": row["rel_end_ms"],
                "y0": row["y_pos"] - 0.4,
                "y1": row["y_pos"] + 0.4,
                "service": row["service_name"],
                "name": row["name"],
                "duration_ms": row["duration_ms"],
                "status": row["status_code"],
                "color": color,
            })

        segments_df = pd.DataFrame(segments_data)

        # Create HoloViews Rectangles
        rects = hv.Rectangles(
            segments_df,
            kdims=["x0", "y0", "x1", "y1"],
            vdims=["service", "name", "duration_ms", "status", "color"],
        )

        # Add labels if requested
        elements = [rects]
        if show_labels:
            labels_data = [
                (row["rel_start_ms"] + 2, row["y_pos"], f"{row['name'][:30]}")
                for _, row in df.iterrows()
            ]
            labels = hv.Labels(labels_data).opts(
                text_font_size="8pt",
                text_align="left",
            )
            elements.append(labels)

        # Create y-axis tick labels
        yticks = [(row["y_pos"], f"{row['service_name']}: {row['name'][:20]}")
                  for _, row in df.iterrows()]

        layout = hv.Overlay(elements).opts(
            opts.Rectangles(
                color="color",
                line_color="black",
                line_width=0.5,
                tools=["hover"],
                width=width,
                height=height,
                xlabel="Time (ms)",
                ylabel="Span",
                yticks=yticks,
                title=f"Trace Waterfall: {trace_id or 'unknown'}",
            ),
        )

        return layout

    def _compute_y_positions(self, df: pd.DataFrame) -> pd.Series:
        """Compute y-axis positions for spans based on parent hierarchy.

        Positions spans vertically with children below parents.
        """
        span_to_idx = {row["span_id"]: i for i, row in df.iterrows()}
        positions = pd.Series(index=df.index, dtype=float)

        # Root spans at top
        roots = df[df["parent_span_id"].isna() | (df["parent_span_id"] == "")]
        y = 0

        def assign_position(span_id: str, level: int) -> None:
            nonlocal y
            idx = span_to_idx[span_id]
            positions.iloc[idx] = y
            y -= 1

            # Find and position children
            children = df[df["parent_span_id"] == span_id]
            for _, child in children.iterrows():
                assign_position(child["span_id"], level + 1)

        for _, root in roots.iterrows():
            assign_position(root["span_id"], 0)

        # Handle orphan spans (parent not in trace)
        unassigned = positions.isna()
        for idx in df[unassigned].index:
            positions.iloc[idx] = y
            y -= 1

        return positions

    def latency_heatmap(
        self,
        ddf: dd.DataFrame | pd.DataFrame,
        x_dim: str = "start_time",
        y_dim: str = "service_name",
        aggregator: str = "mean",
        width: int = 900,
        height: int = 400,
        use_datashader: bool = True,
    ) -> hv.Element:
        """Create a latency heatmap visualization.

        Shows latency distribution across time and another dimension.

        Args:
            ddf: Dask or pandas DataFrame with spans
            x_dim: Dimension for x-axis (default: start_time)
            y_dim: Dimension for y-axis (default: service_name)
            aggregator: Aggregation for duration (mean, p99, max)
            width: Chart width in pixels
            height: Chart height in pixels
            use_datashader: Use Datashader for large datasets

        Returns:
            HoloViews Element (HeatMap or rasterized Image)
        """
        # Compute if Dask DataFrame
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        # Convert timestamps
        df = df.copy()
        if "start_time_unix_nano" in df.columns and x_dim == "start_time":
            df["start_time"] = pd.to_datetime(df["start_time_unix_nano"], unit="ns", utc=True)
            x_dim = "start_time"

        df["duration_ms"] = df["duration_ns"] / 1_000_000

        # Aggregate by time bucket and y dimension
        df["time_bucket"] = df[x_dim].dt.floor("1min")

        agg_funcs = {
            "mean": "mean",
            "p99": lambda x: x.quantile(0.99),
            "p95": lambda x: x.quantile(0.95),
            "max": "max",
            "count": "count",
        }
        agg_func = agg_funcs.get(aggregator, "mean")

        pivot = df.pivot_table(
            index=y_dim,
            columns="time_bucket",
            values="duration_ms",
            aggfunc=agg_func,
            fill_value=0,
        )

        # Convert to HoloViews format
        heatmap_data = []
        for y_val in pivot.index:
            for x_val in pivot.columns:
                heatmap_data.append((x_val, y_val, pivot.loc[y_val, x_val]))

        heatmap = hv.HeatMap(heatmap_data, kdims=["time", y_dim], vdims=["duration_ms"])

        return heatmap.opts(
            opts.HeatMap(
                width=width,
                height=height,
                colorbar=True,
                cmap="viridis",
                tools=["hover"],
                xlabel="Time",
                ylabel=y_dim.replace("_", " ").title(),
                title=f"Latency Heatmap ({aggregator})",
                xrotation=45,
            )
        )

    def latency_distribution(
        self,
        ddf: dd.DataFrame | pd.DataFrame,
        group_by: str = "service_name",
        width: int = 700,
        height: int = 400,
    ) -> hv.Element:
        """Create a latency distribution visualization (box plot).

        Args:
            ddf: Dask or pandas DataFrame with spans
            group_by: Dimension to group by
            width: Chart width
            height: Chart height

        Returns:
            HoloViews BoxWhisker plot
        """
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        df = df.copy()
        df["duration_ms"] = df["duration_ns"] / 1_000_000

        # Create box plot data
        boxwhisker = hv.BoxWhisker(df, kdims=[group_by], vdims=["duration_ms"])

        return boxwhisker.opts(
            opts.BoxWhisker(
                width=width,
                height=height,
                box_fill_color=hv.dim(group_by).categorize(
                    {s: c for s, c in zip(df[group_by].unique(), SERVICE_COLORS)}
                ),
                xlabel=group_by.replace("_", " ").title(),
                ylabel="Duration (ms)",
                title="Latency Distribution by Service",
                xrotation=45,
            )
        )

    def error_timeline(
        self,
        ddf: dd.DataFrame | pd.DataFrame,
        width: int = 900,
        height: int = 300,
    ) -> hv.Element:
        """Create timeline showing error occurrences.

        Args:
            ddf: Dask or pandas DataFrame with spans
            width: Chart width
            height: Chart height

        Returns:
            HoloViews scatter plot of errors
        """
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        # Filter to errors only
        errors = df[df["status_code"] == "ERROR"].copy()

        if len(errors) == 0:
            return hv.Text(0.5, 0.5, "No errors in time range").opts(
                width=width, height=height
            )

        errors["start_time"] = pd.to_datetime(
            errors["start_time_unix_nano"], unit="ns", utc=True
        )

        scatter = hv.Scatter(
            errors,
            kdims=["start_time"],
            vdims=["service_name", "name", "status_message"],
        )

        return scatter.opts(
            opts.Scatter(
                width=width,
                height=height,
                color="service_name",
                cmap="Category10",
                size=8,
                tools=["hover"],
                xlabel="Time",
                ylabel="Service",
                title="Error Timeline",
                legend_position="right",
            )
        )

    def span_count_timeseries(
        self,
        ddf: dd.DataFrame | pd.DataFrame,
        interval: str = "1min",
        group_by: str | None = "service_name",
        width: int = 900,
        height: int = 300,
    ) -> hv.Element:
        """Create time series of span counts.

        Args:
            ddf: Dask or pandas DataFrame with spans
            interval: Time bucket interval
            group_by: Optional dimension to group by
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Curve or NdOverlay of curves
        """
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        df = df.copy()
        df["time_bucket"] = pd.to_datetime(
            df["start_time_unix_nano"], unit="ns", utc=True
        ).dt.floor(interval)

        if group_by:
            counts = df.groupby([group_by, "time_bucket"]).size().reset_index(name="count")
            curves = {
                name: hv.Curve(
                    group[["time_bucket", "count"]].values,
                    kdims=["time"],
                    vdims=["count"],
                    label=name,
                )
                for name, group in counts.groupby(group_by)
            }
            plot = hv.NdOverlay(curves)
        else:
            counts = df.groupby("time_bucket").size().reset_index(name="count")
            plot = hv.Curve(counts, kdims=["time_bucket"], vdims=["count"])

        return plot.opts(
            opts.Curve(
                width=width,
                height=height,
                xlabel="Time",
                ylabel="Span Count",
                title="Spans per Minute",
                legend_position="right",
            ),
            opts.NdOverlay(legend_position="right"),
        )

    def flame_graph(
        self,
        ddf: dd.DataFrame | pd.DataFrame,
        aggregator: str = "duration_sum",
        width: int = 900,
        height: int = 400,
    ) -> hv.Element:
        """Create aggregated flame graph visualization.

        Aggregates spans by service and operation to show where time is spent.

        Args:
            ddf: Dask or pandas DataFrame with spans
            aggregator: How to aggregate (duration_sum, count, avg_duration)
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Bars showing aggregated time per operation
        """
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        df = df.copy()
        df["duration_ms"] = df["duration_ns"] / 1_000_000

        # Aggregate by service and operation
        if aggregator == "duration_sum":
            agg = df.groupby(["service_name", "name"])["duration_ms"].sum()
            value_label = "Total Duration (ms)"
        elif aggregator == "count":
            agg = df.groupby(["service_name", "name"]).size()
            value_label = "Count"
        else:  # avg_duration
            agg = df.groupby(["service_name", "name"])["duration_ms"].mean()
            value_label = "Avg Duration (ms)"

        agg = agg.reset_index(name="value")
        agg = agg.sort_values("value", ascending=False).head(20)

        # Create label combining service and operation
        agg["label"] = agg["service_name"] + ": " + agg["name"]

        bars = hv.Bars(agg, kdims=["label"], vdims=["value", "service_name"])

        return bars.opts(
            opts.Bars(
                width=width,
                height=height,
                color="service_name",
                cmap="Category10",
                xlabel="Operation",
                ylabel=value_label,
                title=f"Flame Graph ({aggregator})",
                xrotation=45,
                invert_axes=True,
            )
        )

    def trace_comparison(
        self,
        trace_ids: list[str],
        width: int = 900,
        height: int = 600,
    ) -> hv.Layout:
        """Compare multiple traces side by side.

        Args:
            trace_ids: List of trace IDs to compare
            width: Total width
            height: Total height

        Returns:
            HoloViews Layout with waterfall charts
        """
        if self.dataset is None:
            raise ValueError("Dataset required for trace_comparison")

        plots = []
        for trace_id in trace_ids[:4]:  # Limit to 4 traces
            waterfall = self.waterfall(
                trace_id=trace_id,
                width=width // 2,
                height=height // 2,
            )
            plots.append(waterfall)

        # Arrange in 2x2 grid
        if len(plots) <= 2:
            return hv.Layout(plots).cols(2)
        return hv.Layout(plots).cols(2)
