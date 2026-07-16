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
        self._catalog: dict | None = None  # last S3 _active_dataset.json
        self._catalog_ts: float = 0.0
        self._auto_load_attempted: bool = False

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

    def discover_catalog(self, *, force: bool = False) -> dict:
        """Read s3://{bucket}/_active_dataset.json (cached ~15s).

        This is the same marker the Panel UI uses — data on disk does not
        imply the engine has loaded a Dask frame; chk previously only
        reported the latter, so a seeded bucket looked like "no dataset".
        """
        import time

        now = time.time()
        if (
            not force
            and self._catalog is not None
            and (now - self._catalog_ts) < 15.0
        ):
            return self._catalog

        info: dict = {"dataset": "", "phase": "missing", "path": "", "error": ""}
        if not S3_BUCKET:
            info["error"] = "S3_BUCKET not set"
            self._catalog, self._catalog_ts = info, now
            return info

        try:
            import s3fs

            fs = s3fs.S3FileSystem(**_get_storage_options())
            marker = f"{S3_BUCKET}/_active_dataset.json"
            if not fs.exists(marker):
                info["error"] = f"no {marker}"
                self._catalog, self._catalog_ts = info, now
                return info
            import json

            raw = fs.cat(marker)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            marker_obj = json.loads(raw)
            name = (marker_obj.get("dataset") or "").strip()
            if not name:
                info["error"] = "marker has no dataset key"
                self._catalog, self._catalog_ts = info, now
                return info
            # Confirm spans/ has at least one parquet (cheap prefix ls, not full tree).
            spans = f"{S3_BUCKET}/{name}/spans"
            n_parts = 0
            try:
                # find is recursive but otel-minimal is small; cap via detail walk
                entries = fs.find(spans)
                n_parts = sum(1 for e in entries if str(e).endswith(".parquet"))
            except Exception as e:
                logger.debug("spans ls %s: %s", spans, e)
            info = {
                "dataset": name,
                "phase": marker_obj.get("phase") or "available",
                "path": f"s3://{S3_BUCKET}/{name}/",
                "partitions": n_parts,
                "span_count": marker_obj.get("span_count"),
                "error": "" if n_parts > 0 else "marker ok but no parquet under spans/",
            }
            if n_parts == 0 and not info["error"]:
                info["error"] = "no parquet under spans/"
        except Exception as e:
            logger.warning("catalog discover failed: %s", e)
            info["error"] = str(e)

        self._catalog, self._catalog_ts = info, now
        return info

    def ensure_active_loaded(self) -> dict | None:
        """If nothing is loaded, auto-bind the catalog dataset into Dask once.

        Returns load result dict, or None if skipped / nothing to load.
        """
        if self._current_df is not None:
            return None
        if self._auto_load_attempted:
            return None
        self._auto_load_attempted = True
        cat = self.discover_catalog(force=True)
        name = (cat.get("dataset") or "").strip()
        if not name or cat.get("error"):
            logger.info(
                "auto-load skipped (catalog=%s error=%s)",
                name or "none",
                cat.get("error") or "",
            )
            return None
        try:
            logger.info("auto-loading active dataset %s", name)
            return self._load_dataset_sync(name)
        except Exception as e:
            logger.warning("auto-load %s failed: %s", name, e)
            return None

    def get_status(self) -> dict:
        """Get Dask cluster status (sync, called from asyncio via executor)."""
        # Bind catalog dataset into Dask on first status if idle — makes `chk`
        # reflect reality without requiring a manual `load`.
        try:
            self.ensure_active_loaded()
        except Exception as e:
            logger.debug("ensure_active_loaded: %s", e)

        catalog = self.discover_catalog()
        catalog_name = (catalog.get("dataset") or "").strip()
        catalog_ok = bool(catalog_name) and not catalog.get("error")

        try:
            client = self._get_client()
            info = client.scheduler_info()
            workers = len(info.get("workers", {}))
            processing = sum(
                len(w.get("processing", {}))
                for w in info.get("workers", {}).values()
            )
            loaded = self._current_df is not None
            if loaded:
                phase = "ready"
                ds = self._current_dataset or catalog_name or "none"
                parts = self._current_df.npartitions
            elif catalog_ok:
                phase = "available"
                ds = catalog_name
                parts = int(catalog.get("partitions") or 0)
            else:
                phase = "idle"
                ds = "none"
                parts = 0
            return {
                "workers": workers,
                "processing": processing,
                "dask_connected": True,
                "current_dataset": ds,
                "dataset_phase": phase,
                "partitions": parts,
                "catalog_dataset": catalog_name or "",
                "catalog_error": catalog.get("error") or "",
            }
        except Exception as e:
            logger.warning("Dask status check failed: %s", e)
            # Still surface catalog so chk is not a blank "none" when S3 is fine.
            if self._current_df is not None:
                ds = self._current_dataset or catalog_name or "none"
                parts = getattr(self._current_df, "npartitions", 0) or 0
            elif catalog_ok:
                ds = catalog_name
                parts = int(catalog.get("partitions") or 0)
            else:
                ds, parts = (self._current_dataset or "none"), 0
            return {
                "workers": 0,
                "processing": 0,
                "dask_connected": False,
                "current_dataset": ds,
                "dataset_phase": "disconnected",
                "partitions": parts,
                "catalog_dataset": catalog_name or "",
                "catalog_error": catalog.get("error") or str(e),
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
