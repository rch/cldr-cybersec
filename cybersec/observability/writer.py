"""OTLP to Parquet writer for OpenTelemetry data.

This module provides the OTelWriter class for receiving OTLP data and writing
it to partitioned Parquet files for efficient analytics.

Example:
    from cybersec.observability.writer import OTelWriter

    writer = OTelWriter("s3://cybersec/otel/")

    # Write from OTLP protobuf (from collector or direct)
    writer.write_spans(spans_data)

    # Generate synthetic data for testing
    writer.write_synthetic_spans(count=100_000, services=5)
"""

from __future__ import annotations

import json
import logging
import os
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

from cybersec.observability.schema import (
    LOGS_SCHEMA,
    METRICS_SCHEMA,
    SPAN_EVENTS_SCHEMA,
    SPAN_LINKS_SCHEMA,
    SPANS_SCHEMA,
    SeverityNumber,
    SpanKind,
    StatusCode,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


# Common HTTP methods and routes for synthetic data
HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
HTTP_ROUTES = [
    "/api/users",
    "/api/users/{id}",
    "/api/orders",
    "/api/orders/{id}",
    "/api/products",
    "/api/products/{id}",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/health",
    "/api/metrics",
]
HTTP_STATUS_CODES = [200, 201, 204, 400, 401, 403, 404, 500, 502, 503]

# Service templates for realistic traces
SERVICE_TEMPLATES = [
    {"name": "api-gateway", "kind": SpanKind.SERVER, "operations": ["HTTP request"]},
    {"name": "auth-service", "kind": SpanKind.SERVER, "operations": ["validate_token", "refresh_token", "login"]},
    {"name": "user-service", "kind": SpanKind.SERVER, "operations": ["get_user", "update_user", "list_users"]},
    {"name": "order-service", "kind": SpanKind.SERVER, "operations": ["create_order", "get_order", "process_order"]},
    {"name": "inventory-service", "kind": SpanKind.SERVER, "operations": ["check_stock", "reserve_item", "release_item"]},
    {"name": "payment-service", "kind": SpanKind.SERVER, "operations": ["process_payment", "refund", "validate_card"]},
    {"name": "notification-service", "kind": SpanKind.PRODUCER, "operations": ["send_email", "send_sms", "send_push"]},
    {"name": "database", "kind": SpanKind.CLIENT, "operations": ["SELECT", "INSERT", "UPDATE", "DELETE"]},
    {"name": "cache", "kind": SpanKind.CLIENT, "operations": ["GET", "SET", "DEL", "MGET"]},
    {"name": "message-queue", "kind": SpanKind.PRODUCER, "operations": ["publish", "consume", "ack"]},
]


class OTelWriter:
    """Writer for OpenTelemetry data to Parquet format.

    This class handles:
    - OTLP protobuf parsing
    - Schema normalization
    - Partitioned Parquet file writing
    - Synthetic data generation for testing

    Attributes:
        base_path: Base path for writing data (s3:// or file://)
        storage_options: S3/filesystem options
        row_group_size: Number of rows per Parquet row group
    """

    def __init__(
        self,
        base_path: str,
        storage_options: dict[str, Any] | None = None,
        row_group_size: int = 100_000,
    ) -> None:
        """Initialize OTelWriter.

        Args:
            base_path: Base path for output (s3://bucket/path or /local/path)
            storage_options: S3/filesystem credentials and options
            row_group_size: Rows per Parquet row group (default 100K)
        """
        self.base_path = base_path.rstrip("/")
        self.storage_options = storage_options or {}
        self.row_group_size = row_group_size

        # Initialize filesystem
        self._filesystem = self._init_filesystem()

    def _init_filesystem(self) -> fs.FileSystem:
        """Initialize PyArrow filesystem from configuration."""
        if self.base_path.startswith("s3://"):
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
            s3_opts = {k: v for k, v in s3_opts.items() if v is not None}
            return fs.S3FileSystem(**s3_opts)
        return fs.LocalFileSystem()

    def _get_output_path(
        self,
        data_type: str,
        dt: datetime,
        service_name: str | None = None,
    ) -> str:
        """Build partitioned output path.

        Args:
            data_type: Type of data (spans, metrics, logs)
            dt: Timestamp for partitioning
            service_name: Optional service for sub-partitioning

        Returns:
            Full path including partitions
        """
        base = self.base_path
        if base.startswith("s3://"):
            base = base[5:]  # Remove s3:// prefix for PyArrow

        date_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour

        path_parts = [base, data_type, f"date={date_str}", f"hour={hour:02d}"]
        if service_name:
            path_parts.append(f"service_name={service_name}")

        return "/".join(path_parts)

    def _generate_file_name(self) -> str:
        """Generate unique file name for Parquet file."""
        return f"{uuid.uuid4().hex[:16]}.parquet"

    def data_exists(self, data_type: str = "spans") -> bool:
        """Check if data already exists at the base path.

        Args:
            data_type: Type of data to check (spans, metrics, logs)

        Returns:
            True if any parquet files exist for this data type
        """
        path = self.base_path
        if path.startswith("s3://"):
            path = path[5:]  # Remove s3:// prefix

        try:
            # Check for any files in the data type directory
            data_path = f"{path}/{data_type}"
            file_info = self._filesystem.get_file_info(
                fs.FileSelector(data_path, recursive=True)
            )
            parquet_files = [f for f in file_info if f.path.endswith('.parquet')]
            return len(parquet_files) > 0
        except Exception as e:
            logger.debug(f"Error checking for existing data: {e}")
            return False

    def get_data_stats(self, data_type: str = "spans") -> dict:
        """Get statistics about existing data.

        Args:
            data_type: Type of data to check

        Returns:
            Dictionary with file_count, total_size_bytes, partitions
        """
        path = self.base_path
        if path.startswith("s3://"):
            path = path[5:]

        try:
            data_path = f"{path}/{data_type}"
            file_info = self._filesystem.get_file_info(
                fs.FileSelector(data_path, recursive=True)
            )
            parquet_files = [f for f in file_info if f.path.endswith('.parquet')]

            # Extract unique partitions (date=YYYY-MM-DD directories)
            partitions = set()
            for f in parquet_files:
                parts = f.path.split('/')
                for part in parts:
                    if part.startswith('date='):
                        partitions.add(part)

            return {
                "file_count": len(parquet_files),
                "total_size_bytes": sum(f.size for f in parquet_files),
                "partitions": sorted(partitions),
            }
        except Exception as e:
            logger.debug(f"Error getting data stats: {e}")
            return {"file_count": 0, "total_size_bytes": 0, "partitions": []}

    def write_spans(
        self,
        spans: list[dict[str, Any]],
        partition_by_service: bool = True,
    ) -> list[str]:
        """Write spans to partitioned Parquet files.

        Args:
            spans: List of span dictionaries matching SPANS_SCHEMA
            partition_by_service: Whether to sub-partition by service_name

        Returns:
            List of written file paths
        """
        if not spans:
            return []

        # Group spans by partition key
        partitions: dict[tuple, list[dict]] = {}
        for span in spans:
            # Extract partition key
            dt = datetime.fromtimestamp(
                span["start_time_unix_nano"] / 1_000_000_000,
                tz=timezone.utc,
            )
            date_val = dt.date()
            hour_val = dt.hour

            # Ensure date and hour are set
            span["date"] = date_val
            span["hour"] = hour_val

            service = span.get("service_name", "unknown")
            key = (date_val, hour_val, service) if partition_by_service else (date_val, hour_val)
            if key not in partitions:
                partitions[key] = []
            partitions[key].append(span)

        # Write each partition
        written_files = []
        for key, partition_spans in partitions.items():
            if partition_by_service:
                date_val, hour_val, service = key
            else:
                date_val, hour_val = key
                service = None

            dt = datetime.combine(date_val, datetime.min.time().replace(hour=hour_val))
            output_path = self._get_output_path("spans", dt, service if partition_by_service else None)
            file_name = self._generate_file_name()
            full_path = f"{output_path}/{file_name}"

            # Convert to Arrow table
            table = pa.Table.from_pylist(partition_spans, schema=SPANS_SCHEMA)

            # Write Parquet file
            self._filesystem.create_dir(output_path)
            pq.write_table(
                table,
                full_path,
                filesystem=self._filesystem,
                row_group_size=self.row_group_size,
                compression="snappy",
            )

            written_files.append(full_path)
            logger.info(f"Wrote {len(partition_spans)} spans to {full_path}")

        return written_files

    def write_metrics(
        self,
        metrics: list[dict[str, Any]],
    ) -> list[str]:
        """Write metrics to partitioned Parquet files.

        Args:
            metrics: List of metric dictionaries matching METRICS_SCHEMA

        Returns:
            List of written file paths
        """
        if not metrics:
            return []

        # Group by partition
        partitions: dict[tuple, list[dict]] = {}
        for metric in metrics:
            dt = datetime.fromtimestamp(
                metric["timestamp_unix_nano"] / 1_000_000_000,
                tz=timezone.utc,
            )
            metric["date"] = dt.date()
            metric["hour"] = dt.hour

            key = (metric["date"], metric["hour"])
            if key not in partitions:
                partitions[key] = []
            partitions[key].append(metric)

        # Write partitions
        written_files = []
        for (date_val, hour_val), partition_metrics in partitions.items():
            dt = datetime.combine(date_val, datetime.min.time().replace(hour=hour_val))
            output_path = self._get_output_path("metrics", dt)
            file_name = self._generate_file_name()
            full_path = f"{output_path}/{file_name}"

            table = pa.Table.from_pylist(partition_metrics, schema=METRICS_SCHEMA)

            self._filesystem.create_dir(output_path)
            pq.write_table(
                table,
                full_path,
                filesystem=self._filesystem,
                row_group_size=self.row_group_size,
                compression="snappy",
            )

            written_files.append(full_path)
            logger.info(f"Wrote {len(partition_metrics)} metrics to {full_path}")

        return written_files

    def write_logs(
        self,
        logs: list[dict[str, Any]],
    ) -> list[str]:
        """Write logs to partitioned Parquet files.

        Args:
            logs: List of log dictionaries matching LOGS_SCHEMA

        Returns:
            List of written file paths
        """
        if not logs:
            return []

        # Group by partition
        partitions: dict[tuple, list[dict]] = {}
        for log in logs:
            dt = datetime.fromtimestamp(
                log["time_unix_nano"] / 1_000_000_000,
                tz=timezone.utc,
            )
            log["date"] = dt.date()
            log["hour"] = dt.hour

            key = (log["date"], log["hour"])
            if key not in partitions:
                partitions[key] = []
            partitions[key].append(log)

        # Write partitions
        written_files = []
        for (date_val, hour_val), partition_logs in partitions.items():
            dt = datetime.combine(date_val, datetime.min.time().replace(hour=hour_val))
            output_path = self._get_output_path("logs", dt)
            file_name = self._generate_file_name()
            full_path = f"{output_path}/{file_name}"

            table = pa.Table.from_pylist(partition_logs, schema=LOGS_SCHEMA)

            self._filesystem.create_dir(output_path)
            pq.write_table(
                table,
                full_path,
                filesystem=self._filesystem,
                row_group_size=self.row_group_size,
                compression="snappy",
            )

            written_files.append(full_path)
            logger.info(f"Wrote {len(partition_logs)} logs to {full_path}")

        return written_files

    # -------------------------------------------------------------------------
    # Synthetic Data Generation
    # -------------------------------------------------------------------------

    def _generate_trace_id(self) -> str:
        """Generate a 32-character hex trace ID."""
        return uuid.uuid4().hex

    def _generate_span_id(self) -> str:
        """Generate a 16-character hex span ID."""
        return uuid.uuid4().hex[:16]

    def _generate_attributes(self, span_kind: SpanKind) -> dict[str, Any]:
        """Generate realistic span attributes based on kind."""
        attrs = {}

        if span_kind == SpanKind.SERVER:
            attrs["http.method"] = random.choice(HTTP_METHODS)
            attrs["http.route"] = random.choice(HTTP_ROUTES)
            attrs["http.status_code"] = random.choice(HTTP_STATUS_CODES)
            attrs["http.url"] = f"https://api.example.com{attrs['http.route']}"
        elif span_kind == SpanKind.CLIENT:
            attrs["db.system"] = random.choice(["postgresql", "mysql", "redis", "mongodb"])
            attrs["db.name"] = "production"
            attrs["db.operation"] = random.choice(["SELECT", "INSERT", "UPDATE", "DELETE"])
            attrs["db.statement"] = f"{attrs['db.operation']} FROM table WHERE id = ?"
        elif span_kind in (SpanKind.PRODUCER, SpanKind.CONSUMER):
            attrs["messaging.system"] = random.choice(["kafka", "rabbitmq", "sqs"])
            attrs["messaging.destination"] = random.choice(["orders", "notifications", "events"])
            attrs["messaging.operation"] = "publish" if span_kind == SpanKind.PRODUCER else "consume"

        return attrs

    def _generate_trace(
        self,
        base_time: datetime,
        services: list[dict],
        max_depth: int = 5,
    ) -> list[dict[str, Any]]:
        """Generate a realistic trace with multiple spans.

        Args:
            base_time: Base timestamp for the trace
            services: List of service templates to use
            max_depth: Maximum trace depth

        Returns:
            List of span dictionaries
        """
        trace_id = self._generate_trace_id()
        spans = []

        def generate_span(
            parent_id: str | None,
            depth: int,
            start_ns: int,
        ) -> int:
            """Recursively generate spans, returns end time in ns."""
            if depth > max_depth or not services:
                return start_ns + random.randint(1_000_000, 10_000_000)  # 1-10ms

            service = random.choice(services)
            span_id = self._generate_span_id()
            operation = random.choice(service["operations"])

            # Generate children first to calculate duration
            children_end = start_ns
            num_children = random.randint(0, 3) if depth < max_depth else 0
            child_start = start_ns + random.randint(100_000, 1_000_000)  # 0.1-1ms processing

            for _ in range(num_children):
                child_end = generate_span(span_id, depth + 1, child_start)
                children_end = max(children_end, child_end)
                child_start = child_end + random.randint(100_000, 500_000)  # Gap between children

            # Span ends after children complete
            end_ns = max(children_end, start_ns) + random.randint(100_000, 2_000_000)

            # Determine status (small chance of error)
            is_error = random.random() < 0.05
            status_code = StatusCode.ERROR if is_error else StatusCode.OK

            # Generate attributes
            attrs = self._generate_attributes(service["kind"])

            # Build span dict
            span = {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_id,
                "start_time_unix_nano": start_ns,
                "end_time_unix_nano": end_ns,
                "duration_ns": end_ns - start_ns,
                "name": operation,
                "kind": SpanKind(service["kind"]).name,
                "status_code": status_code.name,
                "status_message": "Error occurred" if is_error else None,
                "service_name": service["name"],
                "service_namespace": "production",
                "service_version": "1.0.0",
                "host_name": f"{service['name']}-{random.randint(1, 5)}.example.com",
                "host_ip": f"10.0.{random.randint(1, 255)}.{random.randint(1, 255)}",
                "attributes_json": json.dumps(attrs),
                "resource_attributes_json": json.dumps({
                    "service.name": service["name"],
                    "deployment.environment": "production",
                }),
                "http_method": attrs.get("http.method"),
                "http_status_code": attrs.get("http.status_code"),
                "http_url": attrs.get("http.url"),
                "http_route": attrs.get("http.route"),
                "http_target": None,
                "db_system": attrs.get("db.system"),
                "db_name": attrs.get("db.name"),
                "db_operation": attrs.get("db.operation"),
                "db_statement": attrs.get("db.statement"),
                "rpc_system": None,
                "rpc_service": None,
                "rpc_method": None,
                "messaging_system": attrs.get("messaging.system"),
                "messaging_destination": attrs.get("messaging.destination"),
                "messaging_operation": attrs.get("messaging.operation"),
                "exception_type": "RuntimeError" if is_error else None,
                "exception_message": "Something went wrong" if is_error else None,
                "events_count": random.randint(0, 3),
                "links_count": 0,
            }
            spans.append(span)
            return end_ns

        # Generate root span
        start_ns = int(base_time.timestamp() * 1_000_000_000)
        generate_span(None, 0, start_ns)

        return spans

    def write_synthetic_spans(
        self,
        count: int = 10_000,
        services: int | list[dict] | None = None,
        start_time: datetime | None = None,
        duration_hours: int = 1,
        if_not_exists: bool = False,
    ) -> list[str]:
        """Generate and write synthetic spans for testing.

        Args:
            count: Number of spans to generate (approximate)
            services: Number of services or list of service configs
            start_time: Start of time range (default: 1 hour ago)
            duration_hours: Duration to spread spans across
            if_not_exists: If True, skip generation if data already exists

        Returns:
            List of written file paths (empty if skipped due to if_not_exists)
        """
        if if_not_exists and self.data_exists("spans"):
            stats = self.get_data_stats("spans")
            logger.info(
                f"Skipping span generation: {stats['file_count']} files already exist "
                f"({stats['total_size_bytes'] / 1024 / 1024:.1f} MB)"
            )
            return []

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)

        # Configure services
        if services is None:
            service_list = SERVICE_TEMPLATES[:5]
        elif isinstance(services, int):
            service_list = SERVICE_TEMPLATES[:services]
        else:
            service_list = services

        # Generate traces
        all_spans = []
        traces_needed = count // 5  # Average ~5 spans per trace
        time_range_ns = duration_hours * 3600 * 1_000_000_000

        for i in range(traces_needed):
            # Spread traces across time range
            offset_ns = random.randint(0, time_range_ns)
            trace_time = start_time + timedelta(microseconds=offset_ns // 1000)
            trace_spans = self._generate_trace(trace_time, service_list)
            all_spans.extend(trace_spans)

            if (i + 1) % 1000 == 0:
                logger.info(f"Generated {len(all_spans)} spans from {i + 1} traces")

        logger.info(f"Generated {len(all_spans)} total spans from {traces_needed} traces")

        # Write to Parquet
        return self.write_spans(all_spans)

    def write_synthetic_metrics(
        self,
        count: int = 10_000,
        metric_names: list[str] | None = None,
        start_time: datetime | None = None,
        duration_hours: int = 1,
        interval_seconds: int = 60,
        if_not_exists: bool = False,
    ) -> list[str]:
        """Generate and write synthetic metrics for testing.

        Args:
            count: Number of metric data points to generate
            metric_names: List of metric names to generate
            start_time: Start of time range
            duration_hours: Duration to spread metrics across
            interval_seconds: Interval between data points
            if_not_exists: If True, skip generation if data already exists

        Returns:
            List of written file paths (empty if skipped due to if_not_exists)
        """
        if if_not_exists and self.data_exists("metrics"):
            stats = self.get_data_stats("metrics")
            logger.info(
                f"Skipping metrics generation: {stats['file_count']} files already exist "
                f"({stats['total_size_bytes'] / 1024 / 1024:.1f} MB)"
            )
            return []

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)

        if metric_names is None:
            metric_names = [
                "http_request_duration_seconds",
                "http_requests_total",
                "process_cpu_seconds_total",
                "process_resident_memory_bytes",
                "db_query_duration_seconds",
            ]

        services = [s["name"] for s in SERVICE_TEMPLATES[:5]]
        metrics = []
        points_per_metric = count // len(metric_names) // len(services)
        time_step = timedelta(seconds=interval_seconds)

        for metric_name in metric_names:
            for service in services:
                current_time = start_time
                for _ in range(points_per_metric):
                    ts_ns = int(current_time.timestamp() * 1_000_000_000)

                    # Determine metric type and generate appropriate values
                    if "duration" in metric_name or "seconds" in metric_name:
                        # Histogram
                        metric = {
                            "metric_name": metric_name,
                            "metric_description": f"Duration of {metric_name.split('_')[0]} operations",
                            "metric_unit": "s",
                            "metric_type": "histogram",
                            "timestamp_unix_nano": ts_ns,
                            "start_time_unix_nano": ts_ns - 60_000_000_000,
                            "value_double": None,
                            "value_int": None,
                            "histogram_count": random.randint(100, 10000),
                            "histogram_sum": random.uniform(10, 1000),
                            "histogram_min": random.uniform(0.001, 0.01),
                            "histogram_max": random.uniform(1, 10),
                            "histogram_bucket_counts": [
                                random.randint(0, 100) for _ in range(10)
                            ],
                            "histogram_explicit_bounds": [
                                0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5
                            ],
                            "summary_count": None,
                            "summary_sum": None,
                            "summary_quantile_values": None,
                            "summary_quantiles": None,
                            "aggregation_temporality": "CUMULATIVE",
                            "is_monotonic": True,
                            "attributes_json": json.dumps({"endpoint": "/api/v1"}),
                            "service_name": service,
                            "service_namespace": "production",
                            "host_name": f"{service}-1.example.com",
                            "resource_attributes_json": json.dumps({"service.name": service}),
                        }
                    elif "total" in metric_name:
                        # Counter
                        metric = {
                            "metric_name": metric_name,
                            "metric_description": f"Total count of {metric_name.split('_')[0]}",
                            "metric_unit": "1",
                            "metric_type": "sum",
                            "timestamp_unix_nano": ts_ns,
                            "start_time_unix_nano": ts_ns - 3600_000_000_000,
                            "value_double": None,
                            "value_int": random.randint(1000, 1000000),
                            "histogram_count": None,
                            "histogram_sum": None,
                            "histogram_min": None,
                            "histogram_max": None,
                            "histogram_bucket_counts": None,
                            "histogram_explicit_bounds": None,
                            "summary_count": None,
                            "summary_sum": None,
                            "summary_quantile_values": None,
                            "summary_quantiles": None,
                            "aggregation_temporality": "CUMULATIVE",
                            "is_monotonic": True,
                            "attributes_json": json.dumps({}),
                            "service_name": service,
                            "service_namespace": "production",
                            "host_name": f"{service}-1.example.com",
                            "resource_attributes_json": json.dumps({"service.name": service}),
                        }
                    else:
                        # Gauge
                        metric = {
                            "metric_name": metric_name,
                            "metric_description": f"Current value of {metric_name}",
                            "metric_unit": "bytes" if "bytes" in metric_name else "1",
                            "metric_type": "gauge",
                            "timestamp_unix_nano": ts_ns,
                            "start_time_unix_nano": None,
                            "value_double": random.uniform(100000, 10000000),
                            "value_int": None,
                            "histogram_count": None,
                            "histogram_sum": None,
                            "histogram_min": None,
                            "histogram_max": None,
                            "histogram_bucket_counts": None,
                            "histogram_explicit_bounds": None,
                            "summary_count": None,
                            "summary_sum": None,
                            "summary_quantile_values": None,
                            "summary_quantiles": None,
                            "aggregation_temporality": None,
                            "is_monotonic": None,
                            "attributes_json": json.dumps({}),
                            "service_name": service,
                            "service_namespace": "production",
                            "host_name": f"{service}-1.example.com",
                            "resource_attributes_json": json.dumps({"service.name": service}),
                        }

                    metrics.append(metric)
                    current_time += time_step

        logger.info(f"Generated {len(metrics)} metric data points")
        return self.write_metrics(metrics)

    def write_synthetic_logs(
        self,
        count: int = 10_000,
        start_time: datetime | None = None,
        duration_hours: int = 1,
        error_rate: float = 0.1,
        if_not_exists: bool = False,
    ) -> list[str]:
        """Generate and write synthetic logs for testing.

        Args:
            count: Number of log records to generate
            start_time: Start of time range
            duration_hours: Duration to spread logs across
            error_rate: Fraction of logs that are errors
            if_not_exists: If True, skip generation if data already exists

        Returns:
            List of written file paths (empty if skipped due to if_not_exists)
        """
        if if_not_exists and self.data_exists("logs"):
            stats = self.get_data_stats("logs")
            logger.info(
                f"Skipping logs generation: {stats['file_count']} files already exist "
                f"({stats['total_size_bytes'] / 1024 / 1024:.1f} MB)"
            )
            return []

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)

        services = [s["name"] for s in SERVICE_TEMPLATES[:5]]
        time_range_ns = duration_hours * 3600 * 1_000_000_000

        log_messages = {
            SeverityNumber.DEBUG: [
                "Processing request",
                "Cache hit for key",
                "Database query executed",
                "Span context propagated",
            ],
            SeverityNumber.INFO: [
                "Request completed successfully",
                "User authenticated",
                "Order created",
                "Payment processed",
            ],
            SeverityNumber.WARN: [
                "Rate limit approaching",
                "Slow query detected",
                "Retry attempt",
                "Connection pool exhausted",
            ],
            SeverityNumber.ERROR: [
                "Request failed",
                "Database connection error",
                "Authentication failed",
                "Payment declined",
            ],
        }

        logs = []
        for _ in range(count):
            offset_ns = random.randint(0, time_range_ns)
            ts_ns = int(start_time.timestamp() * 1_000_000_000) + offset_ns

            # Determine severity
            if random.random() < error_rate:
                severity = random.choice([SeverityNumber.ERROR, SeverityNumber.WARN])
            else:
                severity = random.choice([SeverityNumber.DEBUG, SeverityNumber.INFO])

            service = random.choice(services)
            message = random.choice(log_messages[severity])

            # Sometimes correlate with a trace
            has_trace = random.random() < 0.3
            trace_id = self._generate_trace_id() if has_trace else None
            span_id = self._generate_span_id() if has_trace else None

            log = {
                "time_unix_nano": ts_ns,
                "observed_time_unix_nano": ts_ns + random.randint(1000, 100000),
                "severity_number": severity.value,
                "severity_text": severity.name,
                "body": f"{message}: {uuid.uuid4().hex[:8]}",
                "body_type": "string",
                "trace_id": trace_id,
                "span_id": span_id,
                "trace_flags": 1 if has_trace else None,
                "attributes_json": json.dumps({
                    "thread.id": random.randint(1, 100),
                    "code.filepath": f"/app/src/{service}/handler.py",
                    "code.lineno": random.randint(1, 500),
                }),
                "service_name": service,
                "service_namespace": "production",
                "host_name": f"{service}-{random.randint(1, 5)}.example.com",
                "resource_attributes_json": json.dumps({"service.name": service}),
                "scope_name": f"com.example.{service}",
                "scope_version": "1.0.0",
            }
            logs.append(log)

        logger.info(f"Generated {len(logs)} log records")
        return self.write_logs(logs)


def create_writer_from_env() -> OTelWriter:
    """Create OTelWriter with configuration from environment variables.

    Uses:
        - OTEL_DATA_PATH: Base path (default: s3://cybersec/otel/)
        - AWS_ACCESS_KEY_ID: S3 access key
        - AWS_SECRET_ACCESS_KEY: S3 secret key
        - S3_ENDPOINT: S3 endpoint URL

    Returns:
        Configured OTelWriter instance
    """
    base_path = os.getenv("OTEL_DATA_PATH", "s3://cybersec/otel/")
    storage_options = {
        "key": os.getenv("AWS_ACCESS_KEY_ID"),
        "secret": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "endpoint_url": os.getenv("S3_ENDPOINT"),
    }
    storage_options = {k: v for k, v in storage_options.items() if v is not None}

    return OTelWriter(base_path, storage_options)
