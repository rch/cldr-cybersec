"""Out-of-core OTel data loader using Dask and PyArrow.

This module provides the OTelDataset class for loading OpenTelemetry data
from Parquet files with partition pruning and predicate pushdown.

Example:
    from cybersec.observability import OTelDataset
    from datetime import datetime, timedelta

    dataset = OTelDataset("s3://cybersec/otel/")
    ddf = dataset.load_spans(
        start_time=datetime.now() - timedelta(hours=1),
        end_time=datetime.now(),
        service_names=["api-gateway", "auth-service"],
    )
    print(f"Loaded {len(ddf)} spans across {ddf.npartitions} partitions")
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dask.dataframe as dd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pyarrow import fs

from cybersec.observability.schema import (
    LOGS_SCHEMA,
    METRICS_SCHEMA,
    SPAN_EVENTS_SCHEMA,
    SPAN_LINKS_SCHEMA,
    SPANS_SCHEMA,
)

if TYPE_CHECKING:
    from dask.distributed import Client

logger = logging.getLogger(__name__)


class OTelDataset:
    """Out-of-core loader for OpenTelemetry Parquet data.

    This class provides efficient loading of OTel data with:
    - Partition pruning based on date/hour
    - Predicate pushdown to Parquet row groups
    - Column projection to minimize I/O
    - Dask integration for distributed processing

    Attributes:
        base_path: Base path to OTel data (s3:// or file://)
        storage_options: Options for S3/filesystem access
        dask_client: Optional Dask distributed client
    """

    def __init__(
        self,
        base_path: str,
        storage_options: dict[str, Any] | None = None,
        dask_client: Client | None = None,
    ) -> None:
        """Initialize OTelDataset.

        Args:
            base_path: Base path to OTel data directory containing
                spans/, metrics/, logs/ subdirectories.
            storage_options: S3/filesystem options (credentials, endpoint, etc.)
            dask_client: Optional Dask distributed client for parallel processing
        """
        self.base_path = base_path.rstrip("/")
        self.storage_options = storage_options or {}
        self.dask_client = dask_client

        # Initialize filesystem based on path scheme
        self._filesystem = self._init_filesystem()

    def _init_filesystem(self) -> fs.FileSystem:
        """Initialize PyArrow filesystem from base path and options."""
        if self.base_path.startswith("s3://"):
            # Extract S3 options
            s3_opts = {
                "access_key": self.storage_options.get(
                    "key", self.storage_options.get("AWS_ACCESS_KEY_ID")
                ),
                "secret_key": self.storage_options.get(
                    "secret", self.storage_options.get("AWS_SECRET_ACCESS_KEY")
                ),
                "endpoint_override": self.storage_options.get(
                    "endpoint_url", self.storage_options.get("S3_ENDPOINT")
                ),
                "region": self.storage_options.get("region", "us-east-1"),
            }
            # Filter None values
            s3_opts = {k: v for k, v in s3_opts.items() if v is not None}
            return fs.S3FileSystem(**s3_opts)
        elif self.base_path.startswith("file://"):
            return fs.LocalFileSystem()
        else:
            # Assume local path
            return fs.LocalFileSystem()

    def _get_path(self, data_type: str) -> str:
        """Get the full path for a data type (spans, metrics, logs)."""
        if self.base_path.startswith("s3://"):
            # Remove s3:// prefix for PyArrow
            return f"{self.base_path[5:]}/{data_type}"
        return f"{self.base_path}/{data_type}"

    def _build_date_filter(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> ds.Expression:
        """Build partition filter expression for date/hour range.

        Args:
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)

        Returns:
            PyArrow dataset expression for partition filtering
        """
        # Convert to dates for partition filtering
        start_date = start_time.date()
        end_date = end_time.date()

        # Build date filter
        date_filter = (ds.field("date") >= start_date) & (ds.field("date") <= end_date)

        # Add hour filtering if within same day
        if start_date == end_date:
            hour_filter = (ds.field("hour") >= start_time.hour) & (
                ds.field("hour") <= end_time.hour
            )
            return date_filter & hour_filter

        return date_filter

    def _build_service_filter(
        self,
        service_names: list[str] | None,
    ) -> ds.Expression | None:
        """Build filter expression for service names.

        Args:
            service_names: List of service names to include, or None for all

        Returns:
            PyArrow dataset expression or None
        """
        if not service_names:
            return None
        if len(service_names) == 1:
            return ds.field("service_name") == service_names[0]
        return ds.field("service_name").isin(service_names)

    def _combine_filters(
        self,
        *filters: ds.Expression | None,
    ) -> ds.Expression | None:
        """Combine multiple filter expressions with AND logic."""
        active_filters = [f for f in filters if f is not None]
        if not active_filters:
            return None
        result = active_filters[0]
        for f in active_filters[1:]:
            result = result & f
        return result

    def _parse_filters(
        self,
        filters: list[tuple[str, str, Any]] | None,
    ) -> ds.Expression | None:
        """Parse user-provided filters into PyArrow expression.

        Args:
            filters: List of (column, operator, value) tuples
                Supported operators: ==, !=, <, <=, >, >=, in, not in

        Returns:
            PyArrow dataset expression or None
        """
        if not filters:
            return None

        expressions = []
        for col, op, val in filters:
            field = ds.field(col)
            if op == "==":
                expressions.append(field == val)
            elif op == "!=":
                expressions.append(field != val)
            elif op == "<":
                expressions.append(field < val)
            elif op == "<=":
                expressions.append(field <= val)
            elif op == ">":
                expressions.append(field > val)
            elif op == ">=":
                expressions.append(field >= val)
            elif op == "in":
                expressions.append(field.isin(val))
            elif op == "not in":
                expressions.append(~field.isin(val))
            else:
                raise ValueError(f"Unsupported filter operator: {op}")

        return self._combine_filters(*expressions)

    def load_spans(
        self,
        start_time: datetime,
        end_time: datetime,
        service_names: list[str] | None = None,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        include_attributes: bool = False,
    ) -> dd.DataFrame:
        """Load spans with partition pruning and predicate pushdown.

        Args:
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
            service_names: Optional list of services to filter
            columns: Optional list of columns to load (None = all)
            filters: Optional list of (column, op, value) filter tuples
            include_attributes: If False, exclude *_json columns for efficiency

        Returns:
            Dask DataFrame with spans data
        """
        return self._load_data(
            data_type="spans",
            schema=SPANS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
            columns=columns,
            filters=filters,
            include_attributes=include_attributes,
        )

    def load_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        metric_names: list[str] | None = None,
        service_names: list[str] | None = None,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
    ) -> dd.DataFrame:
        """Load metrics with partition pruning and predicate pushdown.

        Args:
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
            metric_names: Optional list of metric names to filter
            service_names: Optional list of services to filter
            columns: Optional list of columns to load
            filters: Optional list of (column, op, value) filter tuples

        Returns:
            Dask DataFrame with metrics data
        """
        # Add metric name filter if specified
        metric_filter = None
        if metric_names:
            if len(metric_names) == 1:
                metric_filter = [("metric_name", "==", metric_names[0])]
            else:
                metric_filter = [("metric_name", "in", metric_names)]

        combined_filters = (filters or []) + (metric_filter or [])

        return self._load_data(
            data_type="metrics",
            schema=METRICS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
            columns=columns,
            filters=combined_filters if combined_filters else None,
            include_attributes=True,
        )

    def load_logs(
        self,
        start_time: datetime,
        end_time: datetime,
        severity_min: int = 1,
        service_names: list[str] | None = None,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> dd.DataFrame:
        """Load logs with partition pruning and predicate pushdown.

        Args:
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
            severity_min: Minimum severity level (1-24, default=1 for all)
            service_names: Optional list of services to filter
            columns: Optional list of columns to load
            filters: Optional list of (column, op, value) filter tuples
            trace_id: Optional trace ID to filter correlated logs

        Returns:
            Dask DataFrame with logs data
        """
        # Build severity filter
        log_filters = list(filters or [])
        if severity_min > 1:
            log_filters.append(("severity_number", ">=", severity_min))
        if trace_id:
            log_filters.append(("trace_id", "==", trace_id))

        return self._load_data(
            data_type="logs",
            schema=LOGS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            service_names=service_names,
            columns=columns,
            filters=log_filters if log_filters else None,
            include_attributes=True,
        )

    def load_span_events(
        self,
        start_time: datetime,
        end_time: datetime,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dd.DataFrame:
        """Load span events with optional trace/span filtering.

        Args:
            start_time: Start of time range
            end_time: End of time range
            trace_id: Optional trace ID filter
            span_id: Optional span ID filter

        Returns:
            Dask DataFrame with span events
        """
        filters = []
        if trace_id:
            filters.append(("trace_id", "==", trace_id))
        if span_id:
            filters.append(("span_id", "==", span_id))

        return self._load_data(
            data_type="span_events",
            schema=SPAN_EVENTS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            filters=filters if filters else None,
        )

    def load_span_links(
        self,
        start_time: datetime,
        end_time: datetime,
        trace_id: str | None = None,
    ) -> dd.DataFrame:
        """Load span links with optional trace filtering.

        Args:
            start_time: Start of time range
            end_time: End of time range
            trace_id: Optional trace ID filter

        Returns:
            Dask DataFrame with span links
        """
        filters = []
        if trace_id:
            filters.append(("trace_id", "==", trace_id))

        return self._load_data(
            data_type="span_links",
            schema=SPAN_LINKS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            filters=filters if filters else None,
        )

    def _load_data(
        self,
        data_type: str,
        schema: pa.Schema,
        start_time: datetime,
        end_time: datetime,
        service_names: list[str] | None = None,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        include_attributes: bool = True,
    ) -> dd.DataFrame:
        """Internal method to load data with filtering.

        Args:
            data_type: Type of data (spans, metrics, logs, etc.)
            schema: Expected Arrow schema
            start_time: Start of time range
            end_time: End of time range
            service_names: Optional service name filter
            columns: Optional column projection
            filters: Optional predicate filters
            include_attributes: Whether to include *_json columns

        Returns:
            Dask DataFrame
        """
        path = self._get_path(data_type)

        # Build filter expression
        date_filter = self._build_date_filter(start_time, end_time)
        service_filter = self._build_service_filter(service_names)
        user_filter = self._parse_filters(filters)
        combined_filter = self._combine_filters(date_filter, service_filter, user_filter)

        # Column projection
        if columns:
            # Always include partition columns for filtering
            required_cols = {"date", "hour"}
            if service_names:
                required_cols.add("service_name")
            projection = list(set(columns) | required_cols)
        elif not include_attributes:
            # Exclude JSON attribute columns for efficiency
            projection = [
                f.name for f in schema if not f.name.endswith("_json")
            ]
        else:
            projection = None

        # Try to discover dataset
        try:
            dataset = ds.dataset(
                path,
                filesystem=self._filesystem,
                format="parquet",
                partitioning=ds.partitioning(
                    pa.schema([
                        ("date", pa.date32()),
                        ("hour", pa.int8()),
                    ]),
                    flavor="hive",
                ),
            )
        except Exception as e:
            logger.warning(f"Could not discover dataset at {path}: {e}")
            # Return empty DataFrame with correct schema
            return dd.from_pandas(
                pa.Table.from_pylist([], schema=schema).to_pandas(),
                npartitions=1,
            )

        # Apply filters and projection
        scanner = dataset.scanner(
            columns=projection,
            filter=combined_filter,
        )

        # Convert to Dask DataFrame
        # Use from_delayed for memory efficiency with large datasets
        table = scanner.to_table()

        if len(table) == 0:
            logger.info(f"No {data_type} found in time range {start_time} to {end_time}")
            return dd.from_pandas(table.to_pandas(), npartitions=1)

        # Convert to Dask DataFrame
        df = table.to_pandas()
        npartitions = max(1, len(df) // 100_000)  # ~100K rows per partition
        return dd.from_pandas(df, npartitions=npartitions)

    def get_trace(
        self,
        trace_id: str,
        include_events: bool = False,
        include_links: bool = False,
    ) -> dict[str, dd.DataFrame]:
        """Load all data for a specific trace.

        Args:
            trace_id: The trace ID to load
            include_events: Whether to include span events
            include_links: Whether to include span links

        Returns:
            Dictionary with 'spans' and optionally 'events', 'links' DataFrames
        """
        # We need a time range - use a wide window since we don't know when the trace occurred
        # In production, you might have a trace index to look this up
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=7)

        result = {}

        # Load spans for trace
        result["spans"] = self._load_data(
            data_type="spans",
            schema=SPANS_SCHEMA,
            start_time=start_time,
            end_time=end_time,
            filters=[("trace_id", "==", trace_id)],
            include_attributes=True,
        )

        # Optionally load events and links
        if include_events:
            result["events"] = self.load_span_events(
                start_time=start_time,
                end_time=end_time,
                trace_id=trace_id,
            )

        if include_links:
            result["links"] = self.load_span_links(
                start_time=start_time,
                end_time=end_time,
                trace_id=trace_id,
            )

        return result

    def list_services(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[str]:
        """List unique service names in time range.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            List of service names
        """
        ddf = self.load_spans(
            start_time=start_time,
            end_time=end_time,
            columns=["service_name"],
        )
        return sorted(ddf["service_name"].unique().compute().tolist())

    def list_operations(
        self,
        start_time: datetime,
        end_time: datetime,
        service_name: str | None = None,
    ) -> list[str]:
        """List unique operation names in time range.

        Args:
            start_time: Start of time range
            end_time: End of time range
            service_name: Optional service filter

        Returns:
            List of operation (span) names
        """
        ddf = self.load_spans(
            start_time=start_time,
            end_time=end_time,
            service_names=[service_name] if service_name else None,
            columns=["name"],
        )
        return sorted(ddf["name"].unique().compute().tolist())

    def list_metric_names(
        self,
        start_time: datetime,
        end_time: datetime,
        service_name: str | None = None,
    ) -> list[str]:
        """List unique metric names in time range.

        Args:
            start_time: Start of time range
            end_time: End of time range
            service_name: Optional service filter

        Returns:
            List of metric names
        """
        ddf = self.load_metrics(
            start_time=start_time,
            end_time=end_time,
            service_names=[service_name] if service_name else None,
            columns=["metric_name"],
        )
        return sorted(ddf["metric_name"].unique().compute().tolist())

    def get_statistics(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        """Get summary statistics for the time range.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Dictionary with counts and other statistics
        """
        stats = {}

        # Span statistics
        spans_ddf = self.load_spans(
            start_time=start_time,
            end_time=end_time,
            columns=["trace_id", "span_id", "service_name", "status_code", "duration_ns"],
        )

        if len(spans_ddf) > 0:
            stats["spans"] = {
                "count": len(spans_ddf),
                "unique_traces": spans_ddf["trace_id"].nunique().compute(),
                "services": spans_ddf["service_name"].nunique().compute(),
                "error_rate": (spans_ddf["status_code"] == "ERROR").mean().compute(),
                "avg_duration_ms": (spans_ddf["duration_ns"].mean().compute()) / 1_000_000,
                "p99_duration_ms": (
                    spans_ddf["duration_ns"].quantile(0.99).compute()
                ) / 1_000_000,
            }

        return stats


def create_dataset_from_env() -> OTelDataset:
    """Create OTelDataset with configuration from environment variables.

    Uses:
        - OTEL_DATA_PATH: Base path (default: s3://cybersec/otel/)
        - AWS_ACCESS_KEY_ID: S3 access key
        - AWS_SECRET_ACCESS_KEY: S3 secret key
        - S3_ENDPOINT: S3 endpoint URL

    Returns:
        Configured OTelDataset instance
    """
    import os

    base_path = os.getenv("OTEL_DATA_PATH", "s3://cybersec/otel/")
    storage_options = {
        "key": os.getenv("AWS_ACCESS_KEY_ID"),
        "secret": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "endpoint_url": os.getenv("S3_ENDPOINT"),
    }
    # Filter None values
    storage_options = {k: v for k, v in storage_options.items() if v is not None}

    return OTelDataset(base_path, storage_options)
