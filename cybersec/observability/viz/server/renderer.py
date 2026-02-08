"""Datashader renderer with Dask integration.

This module provides the DatashaderSpanRenderer for out-of-core rendering
of large span datasets using Datashader and Dask distributed computing.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cybersec.observability.viz.server.bounds import ViewportBounds
    from cybersec.observability.viz.server.config import ServerConfig

logger = logging.getLogger(__name__)


@dataclass
class RenderResult:
    """Result from datashader rendering.

    Attributes:
        image: Rendered image as PNG bytes
        aggregation_ns: Time spent in aggregation (nanoseconds)
        shade_ns: Time spent in shading (nanoseconds)
        pixels_filled: Number of non-zero pixels in the image
        task_graph_size: Size of Dask task graph
        cache_hit: Whether this was served from cache
    """

    image: bytes
    aggregation_ns: int
    shade_ns: int
    pixels_filled: int
    task_graph_size: int
    cache_hit: bool = False


def _bounds_key(
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    service_filter: tuple[str, ...] | None,
) -> str:
    """Generate a cache key from bounds and filters.

    Args:
        x_range: X axis range
        y_range: Y axis range
        service_filter: Optional tuple of service names

    Returns:
        String key for cache lookup
    """
    services = ",".join(sorted(service_filter)) if service_filter else ""
    return f"{x_range[0]:.6f},{x_range[1]:.6f}:{y_range[0]:.6f},{y_range[1]:.6f}:{services}"


class DatashaderSpanRenderer:
    """Out-of-core span renderer using Datashader and Dask.

    This renderer processes Dask DataFrames directly without calling
    .compute() before aggregation, enabling true out-of-core processing
    for multi-TB datasets.

    Example:
        from distributed import Client
        from cybersec.observability.viz.server.config import ServerConfig

        config = ServerConfig.from_env()
        client = Client(config.dask_scheduler)
        renderer = DatashaderSpanRenderer(config, client)

        result = renderer.render_viewport(
            ddf=dask_dataframe,
            x_range=(start_ts, end_ts),
            y_range=(0, 1000),  # latency in ms
        )
    """

    def __init__(
        self,
        config: ServerConfig,
        dask_client: Any | None = None,
    ):
        """Initialize DatashaderSpanRenderer.

        Args:
            config: Server configuration
            dask_client: Optional Dask distributed.Client instance
        """
        self.config = config
        self.dask_client = dask_client

        # LRU cache for rendered images
        self._render_cache: dict[str, RenderResult] = {}
        self._cache_order: list[str] = []

    def _cache_get(self, key: str) -> RenderResult | None:
        """Get a result from the cache."""
        result = self._render_cache.get(key)
        if result is not None:
            # Move to end (most recently used)
            if key in self._cache_order:
                self._cache_order.remove(key)
            self._cache_order.append(key)
        return result

    def _cache_put(self, key: str, result: RenderResult) -> None:
        """Put a result in the cache, evicting if necessary."""
        # Evict oldest if at capacity
        while len(self._render_cache) >= self.config.cache_size:
            if self._cache_order:
                oldest = self._cache_order.pop(0)
                self._render_cache.pop(oldest, None)
            else:
                break

        self._render_cache[key] = result
        self._cache_order.append(key)

    def render_viewport(
        self,
        ddf: Any,  # dask.dataframe.DataFrame
        x_range: tuple[float, float],
        y_range: tuple[float, float],
        x_col: str = "timestamp_unix",
        y_col: str = "duration_ms",
        agg_col: str | None = None,
        service_filter: list[str] | None = None,
        cmap: str = "fire",
        use_cache: bool = True,
    ) -> RenderResult:
        """Render a viewport of span data using datashader.

        This performs true out-of-core rendering by passing the Dask
        DataFrame directly to datashader, which creates a streaming
        aggregation pipeline.

        Args:
            ddf: Dask DataFrame with span data
            x_range: X axis range (min, max)
            y_range: Y axis range (min, max)
            x_col: Column name for X axis (default: timestamp_unix)
            y_col: Column name for Y axis (default: duration_ms)
            agg_col: Column to aggregate (default: count)
            service_filter: Optional list of services to filter
            cmap: Colormap name ('fire', 'blues', 'viridis')
            use_cache: Whether to use render cache

        Returns:
            RenderResult with PNG image and timing metrics
        """
        import colorcet as cc
        import datashader as ds
        import datashader.transfer_functions as tf
        import numpy as np

        # Check cache
        cache_key = _bounds_key(
            x_range,
            y_range,
            tuple(sorted(service_filter)) if service_filter else None,
        )
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return RenderResult(
                    image=cached.image,
                    aggregation_ns=cached.aggregation_ns,
                    shade_ns=cached.shade_ns,
                    pixels_filled=cached.pixels_filled,
                    task_graph_size=cached.task_graph_size,
                    cache_hit=True,
                )

        # Apply service filter if specified
        if service_filter:
            ddf = ddf[ddf["service_name"].isin(service_filter)]

        # Resolve colormap
        if cmap == "fire":
            colormap = cc.fire
        elif cmap == "blues":
            colormap = cc.blues
        else:
            colormap = cmap  # matplotlib colormap name

        # Create datashader canvas with viewport bounds
        canvas = ds.Canvas(
            plot_width=self.config.canvas_width,
            plot_height=self.config.canvas_height,
            x_range=x_range,
            y_range=y_range,
        )

        # Get task graph size for metrics
        try:
            task_graph_size = len(ddf.__dask_graph__())
        except Exception:
            task_graph_size = 0

        # Time the aggregation phase
        # This is the key - datashader processes Dask partitions as a stream
        agg_start = time.perf_counter_ns()

        if agg_col:
            agg = canvas.points(ddf, x_col, y_col, ds.mean(agg_col))
        else:
            agg = canvas.points(ddf, x_col, y_col, ds.count())

        # Force computation of aggregation
        if hasattr(agg, "compute"):
            agg_result = agg.compute()
        else:
            agg_result = agg

        agg_end = time.perf_counter_ns()

        # Time the shading phase
        shade_start = time.perf_counter_ns()
        img = tf.shade(agg_result, cmap=colormap, how="log")
        shade_end = time.perf_counter_ns()

        # Count non-zero pixels
        if hasattr(img, "values"):
            img_array = img.values
        else:
            img_array = np.array(img)

        if img_array.ndim == 2:
            pixels_filled = int(np.count_nonzero(img_array))
        else:
            # RGBA image - use alpha channel
            pixels_filled = int(np.count_nonzero(img_array[:, :, 3]))

        # Convert to PNG bytes
        png_bytes = self._img_to_png(img)

        result = RenderResult(
            image=png_bytes,
            aggregation_ns=agg_end - agg_start,
            shade_ns=shade_end - shade_start,
            pixels_filled=pixels_filled,
            task_graph_size=task_graph_size,
            cache_hit=False,
        )

        # Cache the result
        if use_cache:
            self._cache_put(cache_key, result)

        logger.info(
            f"Rendered viewport: agg={result.aggregation_ns/1e6:.1f}ms, "
            f"shade={result.shade_ns/1e6:.1f}ms, pixels={pixels_filled}"
        )

        return result

    def render_from_bounds(
        self,
        ddf: Any,
        bounds: "ViewportBounds",
        **kwargs,
    ) -> RenderResult:
        """Render from a ViewportBounds object.

        Args:
            ddf: Dask DataFrame with span data
            bounds: Viewport bounds
            **kwargs: Additional arguments passed to render_viewport

        Returns:
            RenderResult with PNG image and metrics
        """
        return self.render_viewport(
            ddf,
            x_range=bounds.x_range,
            y_range=bounds.y_range,
            **kwargs,
        )

    def _img_to_png(self, img: Any) -> bytes:
        """Convert datashader image to PNG bytes.

        Args:
            img: Datashader image (xarray DataArray)

        Returns:
            PNG bytes
        """
        from PIL import Image as PILImage

        # Convert to PIL Image
        if hasattr(img, "to_pil"):
            pil_img = img.to_pil()
        else:
            # Manual conversion from xarray
            import numpy as np

            if hasattr(img, "values"):
                arr = img.values
            else:
                arr = np.array(img)

            # Ensure RGBA format
            if arr.ndim == 2:
                # Grayscale - expand to RGBA
                arr = np.stack([arr, arr, arr, np.ones_like(arr) * 255], axis=-1)
            elif arr.shape[-1] == 3:
                # RGB - add alpha
                alpha = np.ones(arr.shape[:2] + (1,), dtype=arr.dtype) * 255
                arr = np.concatenate([arr, alpha], axis=-1)

            pil_img = PILImage.fromarray(arr.astype(np.uint8), mode="RGBA")

        # Convert to PNG bytes
        buffer = io.BytesIO()
        pil_img.save(buffer, format="PNG")
        return buffer.getvalue()

    def clear_cache(self) -> int:
        """Clear the render cache.

        Returns:
            Number of entries cleared
        """
        count = len(self._render_cache)
        self._render_cache.clear()
        self._cache_order.clear()
        return count

    @property
    def cache_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dictionary with cache size and max size
        """
        return {
            "size": len(self._render_cache),
            "max_size": self.config.cache_size,
        }
