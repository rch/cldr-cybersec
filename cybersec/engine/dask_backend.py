"""Dask/S3 backend — extracted from otel-navigator.py for engine reuse.

Provides async wrappers around Dask operations so the gRPC servicer can
call them from asyncio without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import partial

logger = logging.getLogger(__name__)

# Environment — same vars used by otel-navigator.py
DASK_SCHEDULER = os.environ.get("DASK_SCHEDULER", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "cybersec")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
OTEL_DATA_PATH = os.environ.get("OTEL_DATA_PATH", f"s3://{S3_BUCKET}/otel-minimal/")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN", "")
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("S3_REGION", ""))


def _get_storage_options() -> dict:
    """Build s3fs storage options from environment."""
    opts: dict = {}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        opts["key"] = AWS_ACCESS_KEY_ID
        opts["secret"] = AWS_SECRET_ACCESS_KEY
        if AWS_SESSION_TOKEN:
            opts["token"] = AWS_SESSION_TOKEN
    if S3_ENDPOINT:
        opts["client_kwargs"] = {"endpoint_url": S3_ENDPOINT}
    if AWS_REGION:
        opts.setdefault("client_kwargs", {})
        opts["client_kwargs"]["region_name"] = AWS_REGION
    return {k: v for k, v in opts.items() if v}


class DaskBackend:
    """Thin async wrapper around Dask client operations."""

    def __init__(self):
        self._client = None
        self._current_dataset: str = ""
        self._current_df = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not DASK_SCHEDULER:
            raise RuntimeError(
                "DASK_SCHEDULER not set. "
                "Set env var or run inside the K8s cluster."
            )
        from distributed import Client
        self._client = Client(DASK_SCHEDULER, timeout="30s")
        return self._client

    def get_status(self) -> dict:
        """Get Dask cluster status (sync, called from asyncio via executor)."""
        try:
            client = self._get_client()
            info = client.scheduler_info()
            workers = len(info.get("workers", {}))
            processing = sum(
                len(w.get("processing", {}))
                for w in info.get("workers", {}).values()
            )
            return {
                "workers": workers,
                "processing": processing,
                "dask_connected": True,
                "current_dataset": self._current_dataset or "none",
                "dataset_phase": "ready" if self._current_df is not None else "idle",
                "partitions": self._current_df.npartitions if self._current_df is not None else 0,
            }
        except Exception as e:
            logger.warning("Dask status check failed: %s", e)
            return {
                "workers": 0,
                "processing": 0,
                "dask_connected": False,
                "current_dataset": self._current_dataset or "none",
                "dataset_phase": "disconnected",
                "partitions": 0,
            }

    async def status(self) -> dict:
        """Async wrapper for get_status."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_status)

    def _load_dataset_sync(self, dataset_name: str, data_path: str | None = None) -> dict:
        """Load a dataset into the Dask client (sync)."""
        import dask.dataframe as dd

        client = self._get_client()
        path = data_path or f"s3://{S3_BUCKET}/{dataset_name}/spans/"
        storage_opts = _get_storage_options()

        logger.info("Loading dataset %s from %s", dataset_name, path)
        df = dd.read_parquet(path, storage_options=storage_opts)
        self._current_df = df
        self._current_dataset = dataset_name

        return {
            "dataset": dataset_name,
            "partitions": df.npartitions,
            "total_rows": 0,  # Expensive to compute; skip for now
            "path": path,
        }

    async def load_dataset(self, dataset_name: str, data_path: str | None = None) -> dict:
        """Async wrapper for dataset loading."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._load_dataset_sync, dataset_name, data_path)
        )

    def _query_sync(self, expression: str, time_range: str, limit: int) -> dict:
        """Execute a filter query on the loaded DataFrame (sync)."""
        if self._current_df is None:
            raise RuntimeError("No dataset loaded. Use: load <dataset>")

        df = self._current_df

        # Simple expression filter — supports "column op value" patterns
        # e.g. "duration_ms > 500"
        try:
            if expression and not expression.startswith("*"):
                filtered = df.query(expression)
            else:
                filtered = df
        except Exception as e:
            raise RuntimeError(f"Filter error: {e}. Use pandas query syntax.")

        # Compute head for display
        head = filtered.head(limit)
        row_count = len(head)

        table_text = head.to_string(max_rows=20, max_cols=8) if row_count > 0 else "(no results)"

        return {
            "row_count": row_count,
            "summary": f"Found {row_count:,} spans (showing first {limit})",
            "table_text": table_text,
        }

    async def query(self, expression: str, time_range: str = "", limit: int = 100) -> dict:
        """Async wrapper for query."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._query_sync, expression, time_range, limit)
        )

    def _export_sync(self, format: str, destination: str | None, expression: str | None) -> dict:
        """Export data to S3 (sync)."""
        if self._current_df is None:
            raise RuntimeError("No dataset loaded. Use: load <dataset>")

        df = self._current_df
        if expression:
            df = df.query(expression)

        storage_opts = _get_storage_options()
        dest = destination or f"s3://{S3_BUCKET}/exports/{self._current_dataset}"

        if format == "csv":
            path = f"{dest}.csv"
            df.to_csv(path, storage_options=storage_opts, single_file=True)
        elif format == "parquet":
            path = f"{dest}.parquet"
            df.to_parquet(path, storage_options=storage_opts)
        elif format == "json":
            path = f"{dest}.json"
            df.to_json(path, storage_options=storage_opts)
        else:
            raise ValueError(f"Unsupported format: {format}")

        return {"url": path, "row_count": 0, "format": format}

    async def export_data(self, format: str, destination: str | None = None, expression: str | None = None) -> dict:
        """Async wrapper for export."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._export_sync, format, destination, expression)
        )
