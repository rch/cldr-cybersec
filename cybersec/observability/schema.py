"""Arrow schemas for OpenTelemetry data stored in Parquet format.

This module defines the canonical schemas for spans, metrics, and logs
following the OpenTelemetry data model with optimizations for analytical queries.

Partitioning Strategy:
    s3://cybersec/otel/spans/
    ├── date=2026-02-03/
    │   ├── hour=00/
    │   │   ├── service_name=api-gateway/*.parquet
    │   │   └── service_name=auth-service/*.parquet
    │   └── hour=01/
    └── date=2026-02-04/

Design Decisions:
    - Timestamps stored as int64 (nanoseconds) for Arrow compatibility
    - Enums use dictionary encoding for efficient storage and filtering
    - Common attributes extracted to top-level columns for predicate pushdown
    - Full attributes preserved as JSON for flexibility
    - Resource fields denormalized for query efficiency
"""

from enum import IntEnum

import pyarrow as pa


class SpanKind(IntEnum):
    """OpenTelemetry span kind enumeration."""

    UNSPECIFIED = 0
    INTERNAL = 1
    SERVER = 2
    CLIENT = 3
    PRODUCER = 4
    CONSUMER = 5


class StatusCode(IntEnum):
    """OpenTelemetry span status code enumeration."""

    UNSET = 0
    OK = 1
    ERROR = 2


class SeverityNumber(IntEnum):
    """OpenTelemetry log severity numbers (1-24 scale)."""

    UNSPECIFIED = 0
    TRACE = 1
    TRACE2 = 2
    TRACE3 = 3
    TRACE4 = 4
    DEBUG = 5
    DEBUG2 = 6
    DEBUG3 = 7
    DEBUG4 = 8
    INFO = 9
    INFO2 = 10
    INFO3 = 11
    INFO4 = 12
    WARN = 13
    WARN2 = 14
    WARN3 = 15
    WARN4 = 16
    ERROR = 17
    ERROR2 = 18
    ERROR3 = 19
    ERROR4 = 20
    FATAL = 21
    FATAL2 = 22
    FATAL3 = 23
    FATAL4 = 24


# Span kind dictionary type for efficient enum storage
_span_kind_dict = pa.dictionary(pa.int8(), pa.string())
_status_code_dict = pa.dictionary(pa.int8(), pa.string())
_severity_dict = pa.dictionary(pa.int8(), pa.string())
_metric_type_dict = pa.dictionary(pa.int8(), pa.string())


SPANS_SCHEMA = pa.schema(
    [
        # Identity fields
        pa.field("trace_id", pa.string(), nullable=False),
        pa.field("span_id", pa.string(), nullable=False),
        pa.field("parent_span_id", pa.string(), nullable=True),
        # Timing (stored as int64 nanoseconds for Arrow compatibility)
        pa.field("start_time_unix_nano", pa.int64(), nullable=False),
        pa.field("end_time_unix_nano", pa.int64(), nullable=False),
        pa.field("duration_ns", pa.int64(), nullable=False),  # Derived: end - start
        # Classification
        pa.field("name", pa.string(), nullable=False),
        pa.field("kind", _span_kind_dict, nullable=False),
        pa.field("status_code", _status_code_dict, nullable=False),
        pa.field("status_message", pa.string(), nullable=True),
        # Resource (denormalized for query efficiency)
        pa.field("service_name", pa.string(), nullable=False),
        pa.field("service_namespace", pa.string(), nullable=True),
        pa.field("service_version", pa.string(), nullable=True),
        pa.field("host_name", pa.string(), nullable=True),
        pa.field("host_ip", pa.string(), nullable=True),
        # Full attributes as JSON for flexibility
        pa.field("attributes_json", pa.string(), nullable=True),
        pa.field("resource_attributes_json", pa.string(), nullable=True),
        # Common span attributes extracted for efficient filtering
        # HTTP
        pa.field("http_method", pa.string(), nullable=True),
        pa.field("http_status_code", pa.int16(), nullable=True),
        pa.field("http_url", pa.string(), nullable=True),
        pa.field("http_route", pa.string(), nullable=True),
        pa.field("http_target", pa.string(), nullable=True),
        # Database
        pa.field("db_system", pa.string(), nullable=True),
        pa.field("db_name", pa.string(), nullable=True),
        pa.field("db_operation", pa.string(), nullable=True),
        pa.field("db_statement", pa.string(), nullable=True),
        # RPC
        pa.field("rpc_system", pa.string(), nullable=True),
        pa.field("rpc_service", pa.string(), nullable=True),
        pa.field("rpc_method", pa.string(), nullable=True),
        # Messaging
        pa.field("messaging_system", pa.string(), nullable=True),
        pa.field("messaging_destination", pa.string(), nullable=True),
        pa.field("messaging_operation", pa.string(), nullable=True),
        # Exception info
        pa.field("exception_type", pa.string(), nullable=True),
        pa.field("exception_message", pa.string(), nullable=True),
        # Events count (events stored separately if needed)
        pa.field("events_count", pa.int32(), nullable=True),
        pa.field("links_count", pa.int32(), nullable=True),
        # Partitioning columns
        pa.field("date", pa.date32(), nullable=False),
        pa.field("hour", pa.int8(), nullable=False),
    ],
    metadata={
        "otel_version": "1.0",
        "schema_version": "1",
        "description": "OpenTelemetry spans with extracted attributes for analytics",
    },
)


METRICS_SCHEMA = pa.schema(
    [
        # Identity
        pa.field("metric_name", pa.string(), nullable=False),
        pa.field("metric_description", pa.string(), nullable=True),
        pa.field("metric_unit", pa.string(), nullable=True),
        pa.field("metric_type", _metric_type_dict, nullable=False),  # gauge, sum, histogram, etc.
        # Timing
        pa.field("timestamp_unix_nano", pa.int64(), nullable=False),
        pa.field("start_time_unix_nano", pa.int64(), nullable=True),  # For cumulative metrics
        # Values (one will be populated based on metric type)
        pa.field("value_double", pa.float64(), nullable=True),  # For gauge/sum
        pa.field("value_int", pa.int64(), nullable=True),  # For integer gauge/sum
        # Histogram fields
        pa.field("histogram_count", pa.uint64(), nullable=True),
        pa.field("histogram_sum", pa.float64(), nullable=True),
        pa.field("histogram_min", pa.float64(), nullable=True),
        pa.field("histogram_max", pa.float64(), nullable=True),
        pa.field("histogram_bucket_counts", pa.list_(pa.uint64()), nullable=True),
        pa.field("histogram_explicit_bounds", pa.list_(pa.float64()), nullable=True),
        # Summary fields (quantiles)
        pa.field("summary_count", pa.uint64(), nullable=True),
        pa.field("summary_sum", pa.float64(), nullable=True),
        pa.field("summary_quantile_values", pa.list_(pa.float64()), nullable=True),
        pa.field("summary_quantiles", pa.list_(pa.float64()), nullable=True),
        # Aggregation temporality for sum/histogram
        pa.field("aggregation_temporality", pa.string(), nullable=True),  # CUMULATIVE, DELTA
        pa.field("is_monotonic", pa.bool_(), nullable=True),
        # Attributes
        pa.field("attributes_json", pa.string(), nullable=True),
        # Resource (denormalized)
        pa.field("service_name", pa.string(), nullable=False),
        pa.field("service_namespace", pa.string(), nullable=True),
        pa.field("host_name", pa.string(), nullable=True),
        pa.field("resource_attributes_json", pa.string(), nullable=True),
        # Partitioning columns
        pa.field("date", pa.date32(), nullable=False),
        pa.field("hour", pa.int8(), nullable=False),
    ],
    metadata={
        "otel_version": "1.0",
        "schema_version": "1",
        "description": "OpenTelemetry metrics with all data types supported",
    },
)


LOGS_SCHEMA = pa.schema(
    [
        # Timing
        pa.field("time_unix_nano", pa.int64(), nullable=False),
        pa.field("observed_time_unix_nano", pa.int64(), nullable=True),
        # Severity
        pa.field("severity_number", pa.int8(), nullable=False),
        pa.field("severity_text", pa.string(), nullable=True),
        # Body
        pa.field("body", pa.string(), nullable=True),
        pa.field("body_type", pa.string(), nullable=True),  # string, map, array
        # Trace correlation
        pa.field("trace_id", pa.string(), nullable=True),
        pa.field("span_id", pa.string(), nullable=True),
        pa.field("trace_flags", pa.int8(), nullable=True),
        # Attributes
        pa.field("attributes_json", pa.string(), nullable=True),
        # Resource (denormalized)
        pa.field("service_name", pa.string(), nullable=False),
        pa.field("service_namespace", pa.string(), nullable=True),
        pa.field("host_name", pa.string(), nullable=True),
        pa.field("resource_attributes_json", pa.string(), nullable=True),
        # Instrumentation scope
        pa.field("scope_name", pa.string(), nullable=True),
        pa.field("scope_version", pa.string(), nullable=True),
        # Partitioning columns
        pa.field("date", pa.date32(), nullable=False),
        pa.field("hour", pa.int8(), nullable=False),
    ],
    metadata={
        "otel_version": "1.0",
        "schema_version": "1",
        "description": "OpenTelemetry logs with trace correlation",
    },
)


# Span events schema (stored separately for large event payloads)
SPAN_EVENTS_SCHEMA = pa.schema(
    [
        pa.field("trace_id", pa.string(), nullable=False),
        pa.field("span_id", pa.string(), nullable=False),
        pa.field("event_name", pa.string(), nullable=False),
        pa.field("time_unix_nano", pa.int64(), nullable=False),
        pa.field("attributes_json", pa.string(), nullable=True),
        # Partitioning columns
        pa.field("date", pa.date32(), nullable=False),
        pa.field("hour", pa.int8(), nullable=False),
    ],
    metadata={
        "otel_version": "1.0",
        "schema_version": "1",
        "description": "OpenTelemetry span events",
    },
)


# Span links schema (stored separately)
SPAN_LINKS_SCHEMA = pa.schema(
    [
        pa.field("trace_id", pa.string(), nullable=False),
        pa.field("span_id", pa.string(), nullable=False),
        pa.field("linked_trace_id", pa.string(), nullable=False),
        pa.field("linked_span_id", pa.string(), nullable=False),
        pa.field("trace_state", pa.string(), nullable=True),
        pa.field("attributes_json", pa.string(), nullable=True),
        # Partitioning columns
        pa.field("date", pa.date32(), nullable=False),
        pa.field("hour", pa.int8(), nullable=False),
    ],
    metadata={
        "otel_version": "1.0",
        "schema_version": "1",
        "description": "OpenTelemetry span links",
    },
)


# Helper functions for schema operations
def get_partition_columns(schema: pa.Schema) -> list[str]:
    """Get the partition column names from a schema."""
    return [
        field.name
        for field in schema
        if field.name in ("date", "hour", "service_name", "severity_number")
    ]


def get_required_columns(schema: pa.Schema) -> list[str]:
    """Get non-nullable column names from a schema."""
    return [field.name for field in schema if not field.nullable]


def span_kind_to_string(kind: int) -> str:
    """Convert SpanKind integer to string representation."""
    return SpanKind(kind).name


def status_code_to_string(code: int) -> str:
    """Convert StatusCode integer to string representation."""
    return StatusCode(code).name


def severity_to_string(number: int) -> str:
    """Convert severity number to string representation."""
    try:
        return SeverityNumber(number).name
    except ValueError:
        return f"SEVERITY_{number}"
