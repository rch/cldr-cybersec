"""Configuration for Panel visualization server.

This module provides environment-based configuration for the Panel app,
including Dask scheduler connection, S3 credentials, and rendering settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServerConfig:
    """Configuration for the Panel visualization server.

    Attributes:
        dask_scheduler: Address of Dask scheduler (e.g., 'localhost:8786')
        s3_endpoint: S3/MinIO endpoint URL
        s3_access_key: S3 access key
        s3_secret_key: S3 secret key
        otel_data_path: Base path for OTel data (s3:// or file://)
        canvas_width: Width of datashader canvas in pixels
        canvas_height: Height of datashader canvas in pixels
        cache_size: LRU cache size for rendered images
        debounce_ms: Debounce delay for viewport changes in milliseconds
        min_delta_pct: Minimum viewport change percentage to trigger re-render
        warm_start: Whether to pre-warm the renderer on startup
    """

    # Dask cluster configuration
    dask_scheduler: str = "localhost:8786"

    # S3/Storage configuration
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # Data paths
    otel_data_path: str = "s3://cybersec/otel/"

    # Rendering configuration
    canvas_width: int = 800
    canvas_height: int = 400
    cache_size: int = 100
    debounce_ms: int = 100
    min_delta_pct: float = 0.02  # 2% minimum change

    # Server configuration
    warm_start: bool = True
    allowed_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Create configuration from environment variables.

        Environment variables:
            DASK_SCHEDULER: Dask scheduler address (default: localhost:8786)
            S3_ENDPOINT: S3/MinIO endpoint URL
            AWS_ACCESS_KEY_ID or MINIO_ACCESS_KEY: S3 access key
            AWS_SECRET_ACCESS_KEY or MINIO_SECRET_KEY: S3 secret key
            AWS_REGION: S3 region (default: us-east-1)
            OTEL_DATA_PATH: Base path for OTel data (default: s3://cybersec/otel/)
            PANEL_CANVAS_WIDTH: Datashader canvas width (default: 800)
            PANEL_CANVAS_HEIGHT: Datashader canvas height (default: 400)
            PANEL_CACHE_SIZE: LRU cache size (default: 100)
            PANEL_DEBOUNCE_MS: Viewport debounce in ms (default: 100)
            PANEL_MIN_DELTA_PCT: Minimum viewport change (default: 0.02)
            PANEL_WARM_START: Pre-warm renderer (default: true)
            PANEL_ALLOWED_ORIGINS: Comma-separated allowed origins

        Returns:
            ServerConfig instance with values from environment
        """
        # Dask scheduler - support K8s service name or explicit address
        dask_scheduler = os.getenv(
            "DASK_SCHEDULER",
            os.getenv("DASK_SCHEDULER_ADDRESS", "localhost:8786"),
        )

        # S3 credentials - prefer MINIO_* when S3_ENDPOINT is set (local MinIO)
        s3_endpoint = os.getenv("S3_ENDPOINT")
        if s3_endpoint:
            s3_access_key = os.getenv("MINIO_ACCESS_KEY", os.getenv("AWS_ACCESS_KEY_ID"))
            s3_secret_key = os.getenv("MINIO_SECRET_KEY", os.getenv("AWS_SECRET_ACCESS_KEY"))
        else:
            s3_access_key = os.getenv("AWS_ACCESS_KEY_ID")
            s3_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        # Parse allowed origins
        origins_str = os.getenv("PANEL_ALLOWED_ORIGINS", "")
        allowed_origins = [o.strip() for o in origins_str.split(",") if o.strip()]

        return cls(
            dask_scheduler=dask_scheduler,
            s3_endpoint=s3_endpoint,
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            s3_region=os.getenv("AWS_REGION", "us-east-1"),
            otel_data_path=os.getenv("OTEL_DATA_PATH", "s3://cybersec/otel/"),
            canvas_width=int(os.getenv("PANEL_CANVAS_WIDTH", "800")),
            canvas_height=int(os.getenv("PANEL_CANVAS_HEIGHT", "400")),
            cache_size=int(os.getenv("PANEL_CACHE_SIZE", "100")),
            debounce_ms=int(os.getenv("PANEL_DEBOUNCE_MS", "100")),
            min_delta_pct=float(os.getenv("PANEL_MIN_DELTA_PCT", "0.02")),
            warm_start=os.getenv("PANEL_WARM_START", "true").lower() in ("true", "1", "yes"),
            allowed_origins=allowed_origins,
        )

    def get_storage_options(self) -> dict[str, Any]:
        """Get storage options for S3/filesystem access.

        Returns:
            Dictionary suitable for passing to pyarrow/dask storage_options
        """
        options: dict[str, Any] = {}

        if self.s3_endpoint:
            options["endpoint_url"] = self.s3_endpoint

        if self.s3_access_key:
            options["key"] = self.s3_access_key

        if self.s3_secret_key:
            options["secret"] = self.s3_secret_key

        if self.s3_region:
            options["region"] = self.s3_region

        return options

    def get_dask_client_kwargs(self) -> dict[str, Any]:
        """Get kwargs for Dask distributed.Client constructor.

        Returns:
            Dictionary suitable for passing to Client()
        """
        return {
            "address": self.dask_scheduler,
        }
