"""Panel application for OTEL span visualization.

This module provides the SpanExplorerApp, a production-ready Panel application
for interactive visualization of large-scale OTEL span data using Datashader
and Dask distributed computing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import holoviews as hv
import panel as pn
import param

from cybersec.observability.viz.panels import OTelExplorer, TimeRangeSelector, ServiceFilter
from cybersec.observability.viz.server.bounds import BoundsManager, ViewportBounds
from cybersec.observability.viz.server.config import ServerConfig
from cybersec.observability.viz.server.renderer import DatashaderSpanRenderer

if TYPE_CHECKING:
    from distributed import Client

    from cybersec.observability.reader import OTelDataset

logger = logging.getLogger(__name__)

# Enable extensions
hv.extension("bokeh")
pn.extension()


class SpanExplorerApp(param.Parameterized):
    """Panel application for out-of-core OTEL span visualization.

    This extends the OTelExplorer with production capabilities:
    - True out-of-core rendering via Datashader + Dask
    - Debounced viewport recalculation on zoom/pan
    - LRU caching for rendered images
    - Render timing display

    Example:
        from distributed import Client
        from cybersec.observability import OTelDataset
        from cybersec.observability.viz.server import SpanExplorerApp

        client = Client("tcp://scheduler:8786")
        dataset = OTelDataset("s3://cybersec/otel/")
        app = SpanExplorerApp(dataset, dask_client=client)
        app.servable()
    """

    # View mode
    view_mode = param.Selector(
        default="Heatmap",
        objects=["Heatmap", "Overview", "Traces"],
        doc="Current view mode",
    )

    # Render state
    render_in_progress = param.Boolean(default=False, doc="Whether a render is in progress")
    last_render_time_ms = param.Number(default=0.0, doc="Last render time in milliseconds")
    cache_hit = param.Boolean(default=False, doc="Whether last render was a cache hit")

    # Refresh trigger
    refresh = param.Action(
        lambda self: self._refresh_data(),
        doc="Refresh data",
    )

    def __init__(
        self,
        dataset: "OTelDataset",
        config: ServerConfig | None = None,
        dask_client: "Client | None" = None,
        **params,
    ) -> None:
        """Initialize SpanExplorerApp.

        Args:
            dataset: OTelDataset for loading data
            config: Server configuration (defaults to from_env())
            dask_client: Optional Dask distributed.Client
        """
        super().__init__(**params)

        self.dataset = dataset
        self.config = config or ServerConfig.from_env()
        self.dask_client = dask_client

        # Initialize components
        self.time_range = TimeRangeSelector()
        self.service_filter = ServiceFilter()
        self.bounds_manager = BoundsManager(
            debounce_ms=self.config.debounce_ms,
            min_delta_pct=self.config.min_delta_pct,
            on_bounds_change=self._on_bounds_change,
        )
        self.renderer = DatashaderSpanRenderer(self.config, dask_client)

        # Data cache
        self._ddf = None  # Dask DataFrame
        self._data_bounds: ViewportBounds | None = None
        self._current_image: bytes | None = None

    def _refresh_data(self) -> None:
        """Refresh data from dataset."""
        self._ddf = None
        self._data_bounds = None
        self._current_image = None
        self.renderer.clear_cache()
        self._update_services()

    def _update_services(self) -> None:
        """Update available services from current data."""
        try:
            start_time, end_time = self.time_range.time_range
            services = self.dataset.list_services(start_time, end_time)
            self.service_filter.update_services(services)
        except Exception as e:
            logger.warning(f"Failed to update services: {e}")

    def _load_ddf(self):
        """Load spans as Dask DataFrame (no compute)."""
        if self._ddf is not None:
            return self._ddf

        start_time, end_time = self.time_range.time_range
        service_names = self.service_filter.services or None

        self._ddf = self.dataset.load_spans(
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
            include_attributes=False,  # Exclude JSON columns for efficiency
        )

        # Compute data bounds (this triggers computation for min/max)
        self._compute_data_bounds()

        return self._ddf

    def _compute_data_bounds(self) -> None:
        """Compute data bounds from Dask DataFrame."""
        if self._ddf is None:
            return

        try:
            # Compute bounds in parallel
            x_min = self._ddf["timestamp_unix"].min().compute()
            x_max = self._ddf["timestamp_unix"].max().compute()
            y_min = self._ddf["duration_ms"].min().compute()
            y_max = self._ddf["duration_ms"].max().compute()

            # Add 1% padding
            x_pad = (x_max - x_min) * 0.01 if x_max > x_min else 1
            y_pad = (y_max - y_min) * 0.01 if y_max > y_min else 1

            self._data_bounds = ViewportBounds(
                x_min=x_min - x_pad,
                x_max=x_max + x_pad,
                y_min=max(0, y_min - y_pad),  # Duration can't be negative
                y_max=y_max + y_pad,
            )

            # Initialize bounds manager with data bounds
            self.bounds_manager.set_initial_bounds(self._data_bounds)

        except Exception as e:
            logger.error(f"Failed to compute data bounds: {e}")
            # Set default bounds
            self._data_bounds = ViewportBounds(0, 1, 0, 1)

    def _on_bounds_change(self, bounds: ViewportBounds) -> None:
        """Handle viewport bounds change."""
        self._render_heatmap(bounds)

    def _render_heatmap(self, bounds: ViewportBounds | None = None) -> None:
        """Render the latency heatmap."""
        import time

        ddf = self._load_ddf()
        if ddf is None:
            return

        if bounds is None:
            bounds = self._data_bounds

        if bounds is None:
            return

        self.render_in_progress = True
        start_time = time.perf_counter()

        try:
            result = self.renderer.render_from_bounds(
                ddf,
                bounds,
                service_filter=self.service_filter.services or None,
            )
            self._current_image = result.image
            self.cache_hit = result.cache_hit
            self.last_render_time_ms = (time.perf_counter() - start_time) * 1000

        except Exception as e:
            logger.error(f"Render failed: {e}")
            self._current_image = None

        finally:
            self.render_in_progress = False

    @param.depends("view_mode", "time_range.preset", "service_filter.services")
    def main_view(self) -> pn.viewable.Viewable:
        """Generate the main view based on current mode and filters."""
        try:
            if self.view_mode == "Heatmap":
                return self._heatmap_view()
            elif self.view_mode == "Overview":
                return self._overview_view()
            elif self.view_mode == "Traces":
                return self._traces_view()
            else:
                return pn.pane.Markdown("## Unknown View")
        except Exception as e:
            logger.error(f"Error generating view: {e}")
            return pn.pane.Alert(f"Error: {e}", alert_type="danger")

    def _heatmap_view(self) -> pn.viewable.Viewable:
        """Generate the interactive latency heatmap view."""
        # Load data and initial render
        ddf = self._load_ddf()

        if ddf is None:
            return pn.pane.Markdown("## No data available")

        # Initial render if not done
        if self._current_image is None:
            self._render_heatmap()

        # Create heatmap display pane
        if self._current_image:
            heatmap_pane = pn.pane.PNG(
                self._current_image,
                width=self.config.canvas_width,
                height=self.config.canvas_height,
            )
        else:
            heatmap_pane = pn.pane.Markdown("*Loading...*")

        # Create range sliders for zoom control
        x_slider = pn.widgets.RangeSlider.from_param(
            self.bounds_manager.param.x_range,
            name="Time Range",
            format="0.0",
        )
        y_slider = pn.widgets.RangeSlider.from_param(
            self.bounds_manager.param.y_range,
            name="Duration (ms)",
            format="0.1",
        )

        # Render status indicator
        @pn.depends(
            self.param.render_in_progress,
            self.param.last_render_time_ms,
            self.param.cache_hit,
        )
        def status_indicator(in_progress, render_time, cache_hit):
            if in_progress:
                return pn.pane.Markdown("*Rendering...*")
            cache_str = " (cached)" if cache_hit else ""
            return pn.pane.Markdown(f"Render time: {render_time:.1f}ms{cache_str}")

        # Zoom controls
        zoom_in_btn = pn.widgets.Button(name="Zoom In", button_type="default")
        zoom_out_btn = pn.widgets.Button(name="Zoom Out", button_type="default")
        reset_btn = pn.widgets.Button(name="Reset", button_type="default")

        @pn.depends(zoom_in_btn, watch=True)
        def on_zoom_in(event):
            if event:
                bounds = self.bounds_manager.current_bounds
                new_bounds = bounds.scale(0.5)  # Zoom to 50%
                self.bounds_manager.update_bounds(
                    x_range=new_bounds.x_range,
                    y_range=new_bounds.y_range,
                )

        @pn.depends(zoom_out_btn, watch=True)
        def on_zoom_out(event):
            if event:
                bounds = self.bounds_manager.current_bounds
                new_bounds = bounds.scale(2.0)  # Zoom to 200%
                self.bounds_manager.update_bounds(
                    x_range=new_bounds.x_range,
                    y_range=new_bounds.y_range,
                )

        @pn.depends(reset_btn, watch=True)
        def on_reset(event):
            if event and self._data_bounds:
                self.bounds_manager.update_bounds(
                    x_range=self._data_bounds.x_range,
                    y_range=self._data_bounds.y_range,
                )

        return pn.Column(
            pn.pane.Markdown("## Latency Heatmap"),
            pn.Row(zoom_in_btn, zoom_out_btn, reset_btn, status_indicator),
            heatmap_pane,
            pn.Row(x_slider, y_slider),
        )

    def _overview_view(self) -> pn.viewable.Viewable:
        """Generate overview dashboard (delegated to base explorer)."""
        # Use the base OTelExplorer for overview
        base_explorer = OTelExplorer(self.dataset)
        base_explorer.time_range = self.time_range
        base_explorer.service_filter = self.service_filter
        return base_explorer._overview_view()

    def _traces_view(self) -> pn.viewable.Viewable:
        """Generate traces view (delegated to base explorer)."""
        base_explorer = OTelExplorer(self.dataset)
        base_explorer.time_range = self.time_range
        base_explorer.service_filter = self.service_filter
        return base_explorer._traces_view()

    def sidebar(self) -> pn.Column:
        """Generate sidebar with controls."""
        cache_stats = self.renderer.cache_stats

        @pn.depends(self.param.last_render_time_ms)
        def cache_info(render_time):
            stats = self.renderer.cache_stats
            return pn.pane.Markdown(
                f"Cache: {stats['size']}/{stats['max_size']}"
            )

        return pn.Column(
            pn.pane.Markdown("# Span Explorer"),
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
            cache_info,
            width=250,
        )

    def servable(self) -> pn.Template:
        """Create servable Panel application."""
        template = pn.template.FastListTemplate(
            title="OTEL Span Explorer",
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


def create_app(
    config: ServerConfig | None = None,
    dask_client: "Client | None" = None,
) -> SpanExplorerApp:
    """Factory function to create SpanExplorerApp from environment.

    Args:
        config: Optional ServerConfig (defaults to from_env())
        dask_client: Optional Dask distributed.Client

    Returns:
        Configured SpanExplorerApp instance
    """
    from cybersec.observability.reader import OTelDataset

    config = config or ServerConfig.from_env()

    # Create dataset
    dataset = OTelDataset(
        config.otel_data_path,
        storage_options=config.get_storage_options(),
        dask_client=dask_client,
    )

    # Connect to Dask if not provided
    if dask_client is None and config.dask_scheduler:
        try:
            from distributed import Client
            dask_client = Client(config.dask_scheduler)
            logger.info(f"Connected to Dask scheduler: {config.dask_scheduler}")
        except Exception as e:
            logger.warning(f"Could not connect to Dask: {e}")

    return SpanExplorerApp(dataset, config=config, dask_client=dask_client)


# Entry point for `panel serve app.py`
def get_app():
    """Get the Panel application for serving.

    This is the entry point for `panel serve`.
    """
    app = create_app()
    return app.servable()


if __name__ == "__main__":
    # Allow running with `python app.py` for testing
    app = create_app()
    app.show()
elif __name__.startswith("bokeh"):
    # Bokeh server entry point
    pn.serve(get_app())
