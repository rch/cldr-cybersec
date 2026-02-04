"""
Hierarchical drill-down visualizations for OTel data.

Implements zoom-triggered aggregation patterns where the level of detail
adjusts based on the visible time range:
- Zoomed out (hours/days): Region/AZ-level aggregates
- Medium zoom (minutes): Service-level aggregates
- Zoomed in (seconds): Individual spans

Uses HoloViews DynamicMap + Datashader for efficient rendering of large datasets.
"""

from typing import Callable, Optional
import numpy as np
import pandas as pd
import holoviews as hv
from holoviews.operation.datashader import rasterize, datashade, dynspread
from holoviews.streams import RangeXY, RangeX
from holoviews.plotting.links import RangeToolLink

hv.extension("bokeh")


class HierarchyLevel:
    """Enumeration of aggregation hierarchy levels."""
    REGION = "region"
    AZ = "availability_zone"
    SERVICE = "service"
    POD = "pod"
    SPAN = "span"


def determine_hierarchy_level(time_range_seconds: float) -> str:
    """
    Determine the appropriate hierarchy level based on visible time range.

    Args:
        time_range_seconds: Width of visible time window in seconds

    Returns:
        Hierarchy level string (region, az, service, pod, span)
    """
    if time_range_seconds > 86400:  # > 1 day
        return HierarchyLevel.REGION
    elif time_range_seconds > 3600:  # > 1 hour
        return HierarchyLevel.AZ
    elif time_range_seconds > 300:  # > 5 minutes
        return HierarchyLevel.SERVICE
    elif time_range_seconds > 30:  # > 30 seconds
        return HierarchyLevel.POD
    else:
        return HierarchyLevel.SPAN


def aggregate_spans_by_level(
    df: pd.DataFrame,
    level: str,
    time_column: str = "start_time",
    duration_column: str = "duration_ns",
    service_column: str = "service_name",
) -> pd.DataFrame:
    """
    Aggregate span data to the specified hierarchy level.

    Args:
        df: DataFrame with span data
        level: Hierarchy level to aggregate to
        time_column: Name of timestamp column
        duration_column: Name of duration column
        service_column: Name of service column

    Returns:
        Aggregated DataFrame with metrics per group
    """
    if df.empty:
        return pd.DataFrame()

    # Define grouping columns based on level
    if level == HierarchyLevel.REGION:
        # Extract region from service name or use synthetic
        df = df.copy()
        df["_group"] = df[service_column].str.extract(r"(service-\d)")[0].fillna("region-1")
        group_cols = ["_group"]

    elif level == HierarchyLevel.AZ:
        df = df.copy()
        df["_group"] = df[service_column].str.extract(r"(service-\d\d?)")[0].fillna("az-1")
        group_cols = ["_group"]

    elif level == HierarchyLevel.SERVICE:
        group_cols = [service_column]

    elif level == HierarchyLevel.POD:
        # Use host column if available, otherwise service
        if "host" in df.columns:
            group_cols = [service_column, "host"]
        else:
            group_cols = [service_column]

    else:  # SPAN level - no aggregation
        return df

    # Time bucketing based on level
    df = df.copy()
    if level == HierarchyLevel.REGION:
        df["_time_bucket"] = pd.to_datetime(df[time_column]).dt.floor("1h")
    elif level == HierarchyLevel.AZ:
        df["_time_bucket"] = pd.to_datetime(df[time_column]).dt.floor("10min")
    elif level == HierarchyLevel.SERVICE:
        df["_time_bucket"] = pd.to_datetime(df[time_column]).dt.floor("1min")
    else:
        df["_time_bucket"] = pd.to_datetime(df[time_column]).dt.floor("10s")

    # Aggregate metrics
    agg_df = df.groupby(group_cols + ["_time_bucket"]).agg({
        duration_column: ["count", "mean", "max", "std"],
    }).reset_index()

    # Flatten column names
    agg_df.columns = group_cols + ["time", "count", "mean_duration", "max_duration", "std_duration"]

    return agg_df


class DrilldownHeatmap:
    """
    Interactive heatmap with zoom-triggered hierarchy drill-down.

    Displays latency/count data as a heatmap where:
    - X-axis: Time
    - Y-axis: Service/Pod/Region (depends on zoom level)
    - Color: Metric value (latency, count, error rate)
    """

    def __init__(
        self,
        data_loader: Callable[[float, float], pd.DataFrame],
        time_column: str = "start_time_unix_nano",
        duration_column: str = "duration_ns",
        service_column: str = "service_name",
        title: str = "Latency Drill-down",
    ):
        """
        Initialize drill-down heatmap.

        Args:
            data_loader: Function that loads data for a time range (start_ns, end_ns) -> DataFrame
            time_column: Name of timestamp column (nanoseconds)
            duration_column: Name of duration column
            service_column: Name of service column
            title: Plot title
        """
        self.data_loader = data_loader
        self.time_column = time_column
        self.duration_column = duration_column
        self.service_column = service_column
        self.title = title
        self._cache = {}

    def _create_heatmap(self, x_range: Optional[tuple], y_range: Optional[tuple]) -> hv.HeatMap:
        """Create heatmap for the current zoom level."""
        # Determine time range
        if x_range is None or x_range[0] is None:
            # Default to last hour
            end_ns = int(pd.Timestamp.now().timestamp() * 1e9)
            start_ns = end_ns - int(3600 * 1e9)
        else:
            start_ns, end_ns = int(x_range[0]), int(x_range[1])

        # Load data for range
        df = self.data_loader(start_ns, end_ns)

        if df.empty:
            return hv.HeatMap([]).opts(title=f"{self.title} (No data)")

        # Determine hierarchy level from time range
        time_range_seconds = (end_ns - start_ns) / 1e9
        level = determine_hierarchy_level(time_range_seconds)

        # Aggregate data
        agg_df = aggregate_spans_by_level(
            df, level,
            time_column=self.time_column,
            duration_column=self.duration_column,
            service_column=self.service_column,
        )

        if agg_df.empty:
            return hv.HeatMap([]).opts(title=f"{self.title} (No data)")

        # Get group column name
        group_col = agg_df.columns[0]

        # Convert duration to milliseconds for display
        agg_df["latency_ms"] = agg_df["mean_duration"] / 1e6

        # Create heatmap
        heatmap = hv.HeatMap(
            agg_df,
            kdims=["time", group_col],
            vdims=["latency_ms", "count"],
        )

        return heatmap.opts(
            title=f"{self.title} ({level} level)",
            colorbar=True,
            cmap="viridis",
            tools=["hover"],
            width=900,
            height=400,
            xrotation=45,
        )

    def view(self) -> hv.DynamicMap:
        """
        Create the interactive drill-down view.

        Returns:
            DynamicMap that updates on zoom/pan
        """
        range_stream = RangeXY()
        dmap = hv.DynamicMap(self._create_heatmap, streams=[range_stream])
        return dmap


class OverviewDetailView:
    """
    Linked overview + detail visualization.

    Shows a minimap overview of the full dataset with a linked detail view
    that shows high-resolution data for the selected time range.
    """

    def __init__(
        self,
        overview_data: pd.DataFrame,
        detail_loader: Callable[[float, float], pd.DataFrame],
        time_column: str = "start_time_unix_nano",
        value_column: str = "duration_ns",
        service_column: str = "service_name",
    ):
        """
        Initialize overview-detail view.

        Args:
            overview_data: Pre-aggregated data for the overview (full time range)
            detail_loader: Function to load detail data for a time range
            time_column: Name of timestamp column
            value_column: Name of value column to visualize
            service_column: Name of service/grouping column
        """
        self.overview_data = overview_data
        self.detail_loader = detail_loader
        self.time_column = time_column
        self.value_column = value_column
        self.service_column = service_column

    def _create_overview(self) -> hv.Element:
        """Create the minimap overview."""
        if self.overview_data.empty:
            return hv.Curve([]).opts(height=100, title="Overview (No data)")

        # Aggregate to hourly buckets for overview
        df = self.overview_data.copy()
        df["_time"] = pd.to_datetime(df[self.time_column])
        df["_hour"] = df["_time"].dt.floor("1h")

        hourly = df.groupby("_hour").agg({
            self.value_column: ["mean", "count"]
        }).reset_index()
        hourly.columns = ["time", "mean_value", "count"]
        hourly["mean_ms"] = hourly["mean_value"] / 1e6

        # Create area chart for overview
        area = hv.Area(hourly, kdims=["time"], vdims=["mean_ms"])

        return area.opts(
            height=120,
            width=900,
            alpha=0.7,
            color="#1f77b4",
            toolbar="disable",
            title="Overview (drag to select range)",
        )

    def _create_detail(self, x_range: Optional[tuple]) -> hv.Element:
        """Create the detail view for selected range."""
        if x_range is None or x_range[0] is None:
            return hv.Scatter([]).opts(title="Detail (select range above)")

        start_ns, end_ns = int(x_range[0] * 1e9), int(x_range[1] * 1e9)
        df = self.detail_loader(start_ns, end_ns)

        if df.empty:
            return hv.Scatter([]).opts(title="Detail (No data in range)")

        # Convert to display units
        df = df.copy()
        df["_time"] = pd.to_datetime(df[self.time_column])
        df["_duration_ms"] = df[self.value_column] / 1e6

        # Create scatter plot colored by service
        scatter = hv.Scatter(
            df,
            kdims=["_time"],
            vdims=["_duration_ms", self.service_column],
        )

        return scatter.opts(
            title="Detail View",
            color=self.service_column,
            cmap="Category20",
            width=900,
            height=400,
            tools=["hover", "box_select"],
            alpha=0.6,
            size=5,
        )

    def view(self) -> hv.Layout:
        """
        Create the linked overview + detail layout.

        Returns:
            Layout with overview minimap linked to detail view
        """
        overview = self._create_overview()

        range_stream = RangeX(source=overview)
        detail = hv.DynamicMap(self._create_detail, streams=[range_stream])

        return (overview + detail).cols(1).opts(shared_axes=False)


class ServiceLatencyExplorer:
    """
    Multi-service latency explorer with linked views.

    Displays:
    - Heatmap of latency by service over time
    - Latency distribution histogram (updates on selection)
    - Service dependency graph (optional)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        time_column: str = "start_time_unix_nano",
        duration_column: str = "duration_ns",
        service_column: str = "service_name",
    ):
        """
        Initialize service latency explorer.

        Args:
            df: DataFrame with span data
            time_column: Name of timestamp column
            duration_column: Name of duration column
            service_column: Name of service column
        """
        self.df = df
        self.time_column = time_column
        self.duration_column = duration_column
        self.service_column = service_column

    def _prepare_data(self) -> pd.DataFrame:
        """Prepare data for visualization."""
        df = self.df.copy()

        # Convert time to datetime
        df["_time"] = pd.to_datetime(df[self.time_column])
        df["_time_bucket"] = df["_time"].dt.floor("1min")

        # Convert duration to ms
        df["_duration_ms"] = df[self.duration_column] / 1e6

        return df

    def _create_heatmap(self, df: pd.DataFrame) -> hv.HeatMap:
        """Create the latency heatmap."""
        # Aggregate by service and time bucket
        agg = df.groupby([self.service_column, "_time_bucket"]).agg({
            "_duration_ms": ["mean", "count", "max"],
        }).reset_index()
        agg.columns = [self.service_column, "time", "mean_latency", "count", "max_latency"]

        heatmap = hv.HeatMap(
            agg,
            kdims=["time", self.service_column],
            vdims=["mean_latency", "count"],
        )

        return heatmap.opts(
            title="Service Latency Heatmap",
            colorbar=True,
            cmap="RdYlGn_r",  # Red = high latency, Green = low
            tools=["hover", "tap"],
            width=800,
            height=400,
            xrotation=45,
        )

    def _create_distribution(
        self, df: pd.DataFrame, x_range: Optional[tuple], y_range: Optional[tuple]
    ) -> hv.Histogram:
        """Create latency distribution histogram for selected range."""
        # Filter by range if specified
        filtered = df
        if x_range and x_range[0] is not None:
            mask = (df["_time_bucket"] >= pd.Timestamp(x_range[0])) & \
                   (df["_time_bucket"] <= pd.Timestamp(x_range[1]))
            filtered = df[mask]

        if filtered.empty:
            return hv.Histogram([]).opts(title="Latency Distribution (No data)")

        # Create histogram
        frequencies, edges = np.histogram(filtered["_duration_ms"], bins=50)
        hist = hv.Histogram((edges, frequencies))

        return hist.opts(
            title=f"Latency Distribution (n={len(filtered):,})",
            xlabel="Latency (ms)",
            ylabel="Count",
            width=400,
            height=300,
            fill_color="#3182bd",
        )

    def view(self) -> hv.Layout:
        """
        Create the interactive explorer layout.

        Returns:
            Layout with heatmap and linked histogram
        """
        df = self._prepare_data()

        # Create heatmap
        heatmap = self._create_heatmap(df)

        # Create dynamic histogram linked to heatmap selection
        range_stream = RangeXY(source=heatmap)

        def update_histogram(x_range, y_range):
            return self._create_distribution(df, x_range, y_range)

        histogram = hv.DynamicMap(update_histogram, streams=[range_stream])

        # Layout
        return (heatmap + histogram).opts(shared_axes=False)


def create_temporal_drilldown(
    dask_df,
    time_column: str = "start_time_unix_nano",
    duration_column: str = "duration_ns",
    service_column: str = "service_name",
    sample_frac: float = 0.1,
) -> hv.Layout:
    """
    Create a temporal drill-down visualization from a Dask DataFrame.

    This is a convenience function that creates an overview + detail view
    suitable for exploring large OTel datasets.

    Args:
        dask_df: Dask DataFrame with span data
        time_column: Name of timestamp column
        duration_column: Name of duration column
        service_column: Name of service column
        sample_frac: Fraction of data to sample for overview (0.0-1.0)

    Returns:
        Layout with linked overview and detail views
    """
    # Sample for overview (compute to pandas)
    overview_data = dask_df.sample(frac=sample_frac).compute()

    # Detail loader that filters the Dask DataFrame
    def load_detail(start_ns: float, end_ns: float) -> pd.DataFrame:
        filtered = dask_df[
            (dask_df[time_column] >= start_ns) &
            (dask_df[time_column] <= end_ns)
        ]
        # Limit to 10K spans for responsiveness
        return filtered.head(10000, compute=True)

    view = OverviewDetailView(
        overview_data=overview_data,
        detail_loader=load_detail,
        time_column=time_column,
        value_column=duration_column,
        service_column=service_column,
    )

    return view.view()
