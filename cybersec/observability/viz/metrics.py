"""Metrics visualization components using HoloViews.

This module provides metric-specific visualizations:
- Time series plots for gauges and counters
- Histogram visualizations
- Multi-metric dashboards

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.viz.metrics import MetricsVisualizer

    dataset = OTelDataset("s3://cybersec/otel/")
    viz = MetricsVisualizer(dataset)

    # Single metric time series
    plot = viz.timeseries(
        metric_name="http_request_duration_seconds",
        start_time=datetime.now() - timedelta(hours=1),
        end_time=datetime.now(),
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import holoviews as hv
import numpy as np
import pandas as pd
from holoviews import opts

if TYPE_CHECKING:
    import dask.dataframe as dd
    from cybersec.observability.reader import OTelDataset

logger = logging.getLogger(__name__)

# Enable Bokeh extension
hv.extension("bokeh")


class MetricsVisualizer:
    """HoloViews-based metrics visualizations.

    Provides visualization methods for OpenTelemetry metrics data including
    time series, histograms, and aggregated views.

    Attributes:
        dataset: OTelDataset instance for loading data
    """

    def __init__(self, dataset: OTelDataset | None = None) -> None:
        """Initialize MetricsVisualizer.

        Args:
            dataset: Optional OTelDataset for loading metric data
        """
        self.dataset = dataset

    def timeseries(
        self,
        metric_name: str | None = None,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        service_names: list[str] | None = None,
        group_by: str | None = "service_name",
        width: int = 900,
        height: int = 400,
    ) -> hv.Element:
        """Create time series plot for a metric.

        Args:
            metric_name: Name of metric to plot (requires dataset)
            ddf: Pre-loaded DataFrame with metrics
            start_time: Start of time range
            end_time: End of time range
            service_names: Optional service filter
            group_by: Dimension to group by (creates multiple lines)
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Curve or NdOverlay
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_metrics(
                start_time=start_time,
                end_time=end_time,
                metric_names=[metric_name] if metric_name else None,
                service_names=service_names,
            )

        # Compute if Dask
        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No metrics data").opts(
                width=width, height=height
            )

        # Convert timestamp
        df = df.copy()
        df["time"] = pd.to_datetime(df["timestamp_unix_nano"], unit="ns", utc=True)

        # Determine value column based on metric type
        if df["value_double"].notna().any():
            value_col = "value_double"
        elif df["value_int"].notna().any():
            value_col = "value_int"
        elif df["histogram_sum"].notna().any():
            # For histograms, show the sum or mean
            df["value"] = df["histogram_sum"] / df["histogram_count"].replace(0, np.nan)
            value_col = "value"
        else:
            return hv.Text(0.5, 0.5, "No numeric values in metrics").opts(
                width=width, height=height
            )

        # Filter to specific metric if provided
        if metric_name and "metric_name" in df.columns:
            df = df[df["metric_name"] == metric_name]

        title = metric_name or "Metrics"

        if group_by and group_by in df.columns:
            curves = {}
            for name, group in df.groupby(group_by):
                sorted_group = group.sort_values("time")
                curves[str(name)] = hv.Curve(
                    sorted_group[["time", value_col]].values,
                    kdims=["time"],
                    vdims=["value"],
                    label=str(name),
                )
            plot = hv.NdOverlay(curves)
        else:
            df = df.sort_values("time")
            plot = hv.Curve(df, kdims=["time"], vdims=[value_col])

        return plot.opts(
            opts.Curve(
                width=width,
                height=height,
                xlabel="Time",
                ylabel="Value",
                title=title,
                tools=["hover"],
                legend_position="right",
            ),
            opts.NdOverlay(legend_position="right"),
        )

    def histogram(
        self,
        metric_name: str | None = None,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        num_bins: int = 20,
        width: int = 700,
        height: int = 400,
    ) -> hv.Element:
        """Create histogram visualization from histogram metrics.

        Args:
            metric_name: Name of histogram metric
            ddf: Pre-loaded DataFrame with metrics
            start_time: Start of time range
            end_time: End of time range
            num_bins: Number of bins for visualization
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Histogram
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_metrics(
                start_time=start_time,
                end_time=end_time,
                metric_names=[metric_name] if metric_name else None,
            )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No histogram data").opts(
                width=width, height=height
            )

        # Filter to histogram type
        df = df[df["metric_type"] == "histogram"]

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No histogram metrics found").opts(
                width=width, height=height
            )

        # Aggregate histogram buckets
        # For simplicity, use the latest data point
        latest = df.sort_values("timestamp_unix_nano").iloc[-1]

        bucket_counts = latest.get("histogram_bucket_counts")
        explicit_bounds = latest.get("histogram_explicit_bounds")

        if bucket_counts is None or explicit_bounds is None:
            return hv.Text(0.5, 0.5, "Missing histogram bucket data").opts(
                width=width, height=height
            )

        # Create histogram from bucket data
        # Bounds define edges, counts are for each bucket
        hist = hv.Histogram((explicit_bounds, bucket_counts[:-1]))  # Last bucket is +inf

        return hist.opts(
            opts.Histogram(
                width=width,
                height=height,
                xlabel="Value",
                ylabel="Count",
                title=f"Histogram: {metric_name or 'Unknown'}",
                fill_color="#4CAF50",
            )
        )

    def gauge_panel(
        self,
        metric_name: str,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        width: int = 200,
        height: int = 200,
    ) -> hv.Element:
        """Create a simple gauge panel showing current value.

        Args:
            metric_name: Name of gauge metric
            ddf: Pre-loaded DataFrame
            start_time: Start of time range
            end_time: End of time range
            width: Panel width
            height: Panel height

        Returns:
            HoloViews element showing current value
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_metrics(
                start_time=start_time,
                end_time=end_time,
                metric_names=[metric_name],
            )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        # Get latest value
        latest = df.sort_values("timestamp_unix_nano").iloc[-1]

        if pd.notna(latest.get("value_double")):
            value = latest["value_double"]
        elif pd.notna(latest.get("value_int")):
            value = latest["value_int"]
        else:
            value = "N/A"

        # Format value
        if isinstance(value, float):
            if value > 1_000_000:
                display_value = f"{value / 1_000_000:.2f}M"
            elif value > 1_000:
                display_value = f"{value / 1_000:.2f}K"
            else:
                display_value = f"{value:.2f}"
        else:
            display_value = str(value)

        # Create text display
        text = hv.Text(0.5, 0.6, display_value).opts(text_font_size="24pt")
        label = hv.Text(0.5, 0.3, metric_name).opts(text_font_size="10pt")

        return (text * label).opts(
            opts.Text(width=width, height=height),
            opts.Overlay(title=metric_name),
        )

    def multi_metric_grid(
        self,
        metric_names: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cols: int = 2,
        width: int = 400,
        height: int = 250,
    ) -> hv.Layout:
        """Create grid of multiple metric time series.

        Args:
            metric_names: List of metric names to display
            start_time: Start of time range
            end_time: End of time range
            cols: Number of columns in grid
            width: Width per chart
            height: Height per chart

        Returns:
            HoloViews Layout with grid of charts
        """
        if self.dataset is None:
            raise ValueError("Dataset required for multi_metric_grid")

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        plots = []
        for metric_name in metric_names:
            try:
                plot = self.timeseries(
                    metric_name=metric_name,
                    start_time=start_time,
                    end_time=end_time,
                    width=width,
                    height=height,
                )
                plots.append(plot)
            except Exception as e:
                logger.warning(f"Failed to create plot for {metric_name}: {e}")
                plots.append(
                    hv.Text(0.5, 0.5, f"Error: {metric_name}").opts(
                        width=width, height=height
                    )
                )

        return hv.Layout(plots).cols(cols)

    def rate_chart(
        self,
        metric_name: str,
        ddf: dd.DataFrame | pd.DataFrame | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        interval: str = "1min",
        width: int = 900,
        height: int = 400,
    ) -> hv.Element:
        """Create rate/derivative chart for counter metrics.

        Shows per-interval increase rate for monotonic counters.

        Args:
            metric_name: Name of counter metric
            ddf: Pre-loaded DataFrame
            start_time: Start of time range
            end_time: End of time range
            interval: Time interval for rate calculation
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Curve showing rate
        """
        if ddf is None:
            if self.dataset is None:
                raise ValueError("Must provide either ddf or dataset")
            if start_time is None:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)
            if end_time is None:
                end_time = datetime.now(timezone.utc)

            ddf = self.dataset.load_metrics(
                start_time=start_time,
                end_time=end_time,
                metric_names=[metric_name],
            )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        df = df.copy()
        df["time"] = pd.to_datetime(df["timestamp_unix_nano"], unit="ns", utc=True)

        # Get value column
        if df["value_int"].notna().any():
            value_col = "value_int"
        elif df["value_double"].notna().any():
            value_col = "value_double"
        else:
            return hv.Text(0.5, 0.5, "No numeric values").opts(
                width=width, height=height
            )

        # Sort by time
        df = df.sort_values("time")

        # Calculate rate (diff / time_diff)
        df["time_diff"] = df["time"].diff().dt.total_seconds()
        df["value_diff"] = df[value_col].diff()
        df["rate"] = df["value_diff"] / df["time_diff"]

        # Remove invalid rates
        df = df[df["rate"] >= 0]  # Monotonic counter

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "Could not calculate rate").opts(
                width=width, height=height
            )

        curve = hv.Curve(df, kdims=["time"], vdims=["rate"])

        return curve.opts(
            opts.Curve(
                width=width,
                height=height,
                xlabel="Time",
                ylabel="Rate (per second)",
                title=f"Rate: {metric_name}",
                tools=["hover"],
            )
        )

    def comparison_chart(
        self,
        metric_name: str,
        compare_by: str = "service_name",
        aggregation: str = "mean",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        width: int = 700,
        height: int = 400,
    ) -> hv.Element:
        """Create comparison bar chart across a dimension.

        Args:
            metric_name: Name of metric to compare
            compare_by: Dimension to compare across
            aggregation: Aggregation method (mean, max, min, sum)
            start_time: Start of time range
            end_time: End of time range
            width: Chart width
            height: Chart height

        Returns:
            HoloViews Bars
        """
        if self.dataset is None:
            raise ValueError("Dataset required for comparison_chart")

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        ddf = self.dataset.load_metrics(
            start_time=start_time,
            end_time=end_time,
            metric_names=[metric_name],
        )

        if hasattr(ddf, "compute"):
            df = ddf.compute()
        else:
            df = ddf

        if len(df) == 0:
            return hv.Text(0.5, 0.5, "No data").opts(width=width, height=height)

        # Get value column
        if df["value_double"].notna().any():
            value_col = "value_double"
        elif df["value_int"].notna().any():
            value_col = "value_int"
        else:
            return hv.Text(0.5, 0.5, "No numeric values").opts(
                width=width, height=height
            )

        # Aggregate
        agg_df = df.groupby(compare_by)[value_col].agg(aggregation).reset_index()
        agg_df.columns = [compare_by, "value"]

        bars = hv.Bars(agg_df, kdims=[compare_by], vdims=["value"])

        return bars.opts(
            opts.Bars(
                width=width,
                height=height,
                xlabel=compare_by.replace("_", " ").title(),
                ylabel=f"{aggregation.title()} Value",
                title=f"{metric_name} by {compare_by}",
                color="value",
                cmap="viridis",
                xrotation=45,
            )
        )
