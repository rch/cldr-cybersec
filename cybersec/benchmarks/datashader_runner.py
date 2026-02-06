"""True out-of-core datashader rendering for scaling benchmarks.

This module provides datashader rendering that works directly with
Dask DataFrames without calling .compute() before aggregation.
This is the key to out-of-core processing.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .config import BenchmarkConfig
from .metrics import BenchmarkMetrics


@dataclass
class ZoomBounds:
    """Bounding box for zoom region.

    Attributes:
        x_min: Minimum X value (timestamp)
        x_max: Maximum X value (timestamp)
        y_min: Minimum Y value (duration)
        y_max: Maximum Y value (duration)
    """
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def x_range(self) -> tuple[float, float]:
        """X range as tuple."""
        return (self.x_min, self.x_max)

    @property
    def y_range(self) -> tuple[float, float]:
        """Y range as tuple."""
        return (self.y_min, self.y_max)

    def scale(self, fraction: float) -> "ZoomBounds":
        """Create zoomed bounds centered on midpoint.

        Args:
            fraction: Fraction of original range (e.g., 0.1 for 10%)

        Returns:
            New ZoomBounds with scaled range
        """
        x_mid = (self.x_min + self.x_max) / 2
        y_mid = (self.y_min + self.y_max) / 2
        x_half = (self.x_max - self.x_min) * fraction / 2
        y_half = (self.y_max - self.y_min) * fraction / 2

        return ZoomBounds(
            x_min=x_mid - x_half,
            x_max=x_mid + x_half,
            y_min=y_mid - y_half,
            y_max=y_mid + y_half,
        )


@dataclass
class RenderResult:
    """Result from datashader rendering.

    Attributes:
        image: Rendered image (PIL or numpy array)
        aggregation_ns: Time spent in aggregation (nanoseconds)
        shade_ns: Time spent in shading (nanoseconds)
        points_in_view: Number of points in the view
        task_graph_size: Size of Dask task graph
    """
    image: Any  # PIL Image or numpy array
    aggregation_ns: int
    shade_ns: int
    points_in_view: int
    task_graph_size: int


def compute_data_bounds(
    ddf,  # dask.dataframe.DataFrame
    x_col: str = "timestamp_unix",
    y_col: str = "duration_ms",
) -> ZoomBounds:
    """Compute data bounds from Dask DataFrame.

    This triggers a computation to find min/max values.

    Args:
        ddf: Dask DataFrame
        x_col: Column for X axis
        y_col: Column for Y axis

    Returns:
        ZoomBounds with data extent
    """
    import dask.dataframe as dd

    # Compute min/max in parallel
    x_min, x_max = ddf[x_col].min().compute(), ddf[x_col].max().compute()
    y_min, y_max = ddf[y_col].min().compute(), ddf[y_col].max().compute()

    # Add small padding (1%) to avoid edge effects
    x_pad = (x_max - x_min) * 0.01
    y_pad = (y_max - y_min) * 0.01

    return ZoomBounds(
        x_min=x_min - x_pad,
        x_max=x_max + x_pad,
        y_min=y_min - y_pad,
        y_max=y_max + y_pad,
    )


def render_with_zoom(
    ddf,  # dask.dataframe.DataFrame
    bounds: ZoomBounds,
    config: BenchmarkConfig,
    x_col: str = "timestamp_unix",
    y_col: str = "duration_ms",
    agg_col: Optional[str] = None,
    cmap: str = "fire",
) -> RenderResult:
    """Render Dask DataFrame with datashader at specified zoom.

    This is TRUE out-of-core rendering - datashader processes the
    Dask DataFrame directly without calling .compute() first.

    Args:
        ddf: Dask DataFrame with span data
        bounds: Zoom bounds for rendering
        config: Benchmark configuration
        x_col: Column for X axis
        y_col: Column for Y axis
        agg_col: Column to aggregate (default: count)
        cmap: Colormap name

    Returns:
        RenderResult with image and timing
    """
    import datashader as ds
    import datashader.transfer_functions as tf
    import colorcet as cc

    # Resolve colormap - use colorcet if 'fire' or similar
    if cmap == 'fire':
        cmap = cc.fire
    elif cmap == 'blues':
        cmap = cc.blues
    elif cmap == 'viridis':
        cmap = 'viridis'  # matplotlib colormap

    # Create canvas with specified bounds
    canvas = ds.Canvas(
        plot_width=config.canvas_width,
        plot_height=config.canvas_height,
        x_range=bounds.x_range,
        y_range=bounds.y_range,
    )

    # Get task graph size before aggregation
    task_graph_size = len(ddf.__dask_graph__())

    # Time the aggregation phase
    # This is where the out-of-core magic happens - datashader
    # creates a lazy aggregation that processes chunks as needed
    agg_start = time.perf_counter_ns()

    if agg_col:
        agg = canvas.points(ddf, x_col, y_col, ds.mean(agg_col))
    else:
        agg = canvas.points(ddf, x_col, y_col, ds.count())

    # Force computation of aggregation
    # This materializes the aggregated grid
    agg_result = agg.compute() if hasattr(agg, 'compute') else agg

    agg_end = time.perf_counter_ns()

    # Time the shading phase
    shade_start = time.perf_counter_ns()
    img = tf.shade(agg_result, cmap=cmap, how="log")
    shade_end = time.perf_counter_ns()

    # Count non-zero pixels as proxy for points in view
    # The image from shade() is an xarray DataArray, extract values
    if hasattr(img, 'values'):
        img_array = img.values
    else:
        img_array = np.array(img)

    # Count non-zero pixels (aggregation result is 2D)
    if img_array.ndim == 2:
        points_in_view = int(np.count_nonzero(img_array))
    else:
        # RGBA image - use alpha channel
        points_in_view = int(np.count_nonzero(img_array[:, :, 3]))

    return RenderResult(
        image=img,
        aggregation_ns=agg_end - agg_start,
        shade_ns=shade_end - shade_start,
        points_in_view=points_in_view,
        task_graph_size=task_graph_size,
    )


def render_benchmark_iteration(
    ddf,  # dask.dataframe.DataFrame
    zoom_name: str,
    zoom_fraction: float,
    data_bounds: ZoomBounds,
    config: BenchmarkConfig,
    run_id: int,
    worker_count: int,
    is_warmup: bool = False,
) -> BenchmarkMetrics:
    """Run a single benchmark iteration.

    Args:
        ddf: Dask DataFrame
        zoom_name: Name of zoom level
        zoom_fraction: Zoom fraction (1.0 = full, 0.1 = 10%)
        data_bounds: Full data bounds
        config: Benchmark configuration
        run_id: Run identifier
        worker_count: Number of Dask workers
        is_warmup: Whether this is a warmup run

    Returns:
        BenchmarkMetrics with all captured data
    """
    # Compute zoom bounds
    if zoom_fraction >= 1.0:
        bounds = data_bounds
    else:
        bounds = data_bounds.scale(zoom_fraction)

    # Run the render
    wall_start = time.perf_counter_ns()
    result = render_with_zoom(ddf, bounds, config)
    wall_end = time.perf_counter_ns()

    # Estimate points processed
    # At full zoom, all points; at 10% zoom, ~10% of points
    # (This is approximate - actual depends on data distribution)
    total_points = len(ddf)  # This doesn't trigger compute
    estimated_points = int(total_points * zoom_fraction)

    wall_time_s = (wall_end - wall_start) / 1e9
    points_per_sec = estimated_points / wall_time_s if wall_time_s > 0 else 0

    # Estimate GB processed (rough: ~500 bytes per span expanded)
    gb_processed = estimated_points * 500 / (1024**3)

    return BenchmarkMetrics(
        run_id=run_id,
        worker_count=worker_count,
        zoom_level=zoom_name,
        zoom_fraction=zoom_fraction,
        is_warmup=is_warmup,
        wall_clock_ns=wall_end - wall_start,
        aggregation_ns=result.aggregation_ns,
        shade_ns=result.shade_ns,
        points_processed=estimated_points,
        points_per_second=points_per_sec,
        gb_processed=gb_processed,
        gb_per_second=gb_processed / wall_time_s if wall_time_s > 0 else 0,
        task_graph_size=result.task_graph_size,
    )


class DatashaderBenchmark:
    """Benchmark runner for datashader visualization.

    Manages data loading, bounds computation, and benchmark execution.
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        x_col: str = "timestamp_unix",
        y_col: str = "duration_ms",
    ):
        """Initialize benchmark runner.

        Args:
            config: Benchmark configuration
            x_col: Column for X axis
            y_col: Column for Y axis
        """
        self.config = config
        self.x_col = x_col
        self.y_col = y_col
        self._ddf = None
        self._bounds = None

    def load_data(self) -> None:
        """Load data from S3 as Dask DataFrame."""
        import dask.dataframe as dd

        path = self.config.s3_path
        storage_options = {}

        if self.config.s3_endpoint:
            storage_options["client_kwargs"] = {
                "endpoint_url": self.config.s3_endpoint
            }

        if self.config.aws_access_key_id:
            storage_options["key"] = self.config.aws_access_key_id
            storage_options["secret"] = self.config.aws_secret_access_key
        elif self.config.aws_profile:
            storage_options["profile"] = self.config.aws_profile

        self._ddf = dd.read_parquet(
            path,
            storage_options=storage_options if storage_options else None,
        )

    def compute_bounds(self) -> ZoomBounds:
        """Compute and cache data bounds."""
        if self._bounds is None:
            if self._ddf is None:
                self.load_data()
            self._bounds = compute_data_bounds(
                self._ddf, self.x_col, self.y_col
            )
        return self._bounds

    @property
    def ddf(self):
        """Get Dask DataFrame, loading if necessary."""
        if self._ddf is None:
            self.load_data()
        return self._ddf

    def run_iteration(
        self,
        zoom_name: str,
        zoom_fraction: float,
        run_id: int,
        worker_count: int,
        is_warmup: bool = False,
    ) -> BenchmarkMetrics:
        """Run a single benchmark iteration.

        Args:
            zoom_name: Name of zoom level
            zoom_fraction: Zoom fraction
            run_id: Run identifier
            worker_count: Number of workers
            is_warmup: Whether this is warmup

        Returns:
            BenchmarkMetrics for this iteration
        """
        bounds = self.compute_bounds()
        return render_benchmark_iteration(
            self.ddf,
            zoom_name,
            zoom_fraction,
            bounds,
            self.config,
            run_id,
            worker_count,
            is_warmup,
        )
