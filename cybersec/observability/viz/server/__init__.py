"""Panel server module for OTEL span visualization.

This module provides a production-ready Panel application for interactive
visualization of large-scale OTEL span data using Datashader and Dask.

Components:
- SpanExplorerApp: Main Panel application with out-of-core rendering
- DatashaderSpanRenderer: Dask-integrated datashader renderer
- BoundsManager: Debounced viewport management
- ServerConfig: Environment-based configuration

Example:
    # Run as standalone Panel server
    panel serve cybersec/observability/viz/server/app.py --show

    # Or programmatically:
    from cybersec.observability.viz.server import create_app
    app = create_app()
    app.show()

For jupyter-server-proxy integration, this module provides a setup function
that can be registered as an entry point.
"""

__all__ = [
    "SpanExplorerApp",
    "DatashaderSpanRenderer",
    "BoundsManager",
    "ViewportBounds",
    "ServerConfig",
    "create_app",
    "setup_panel_proxy",
]


def __getattr__(name: str):
    """Lazy imports to avoid loading heavy visualization dependencies."""
    if name == "SpanExplorerApp":
        from cybersec.observability.viz.server.app import SpanExplorerApp
        return SpanExplorerApp
    if name == "create_app":
        from cybersec.observability.viz.server.app import create_app
        return create_app
    if name == "DatashaderSpanRenderer":
        from cybersec.observability.viz.server.renderer import DatashaderSpanRenderer
        return DatashaderSpanRenderer
    if name == "BoundsManager":
        from cybersec.observability.viz.server.bounds import BoundsManager
        return BoundsManager
    if name == "ViewportBounds":
        from cybersec.observability.viz.server.bounds import ViewportBounds
        return ViewportBounds
    if name == "ServerConfig":
        from cybersec.observability.viz.server.config import ServerConfig
        return ServerConfig
    if name == "setup_panel_proxy":
        return _setup_panel_proxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _setup_panel_proxy():
    """Setup function for jupyter-server-proxy.

    This function is called by jupyter-server-proxy to configure the
    Panel server as a proxy target.

    Returns:
        Dictionary with proxy configuration
    """
    return {
        "command": [
            "panel",
            "serve",
            "--port",
            "{port}",
            "--allow-websocket-origin",
            "*",
            "--prefix",
            "{base_url}otel-explorer",
            "cybersec/observability/viz/server/app.py",
        ],
        "timeout": 60,
        "launcher_entry": {
            "title": "OTEL Span Explorer",
            "icon_path": None,  # Could add a custom icon
        },
        "new_browser_tab": True,
    }


# Alias for entry point compatibility
setup_panel_proxy = _setup_panel_proxy
