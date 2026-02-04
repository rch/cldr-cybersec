"""Tests for observability schema definitions."""

import pyarrow as pa
import pytest

from cybersec.observability.schema import (
    LOGS_SCHEMA,
    METRICS_SCHEMA,
    SPAN_EVENTS_SCHEMA,
    SPAN_LINKS_SCHEMA,
    SPANS_SCHEMA,
    SeverityNumber,
    SpanKind,
    StatusCode,
    get_partition_columns,
    get_required_columns,
    severity_to_string,
    span_kind_to_string,
    status_code_to_string,
)


class TestSpansSchema:
    """Tests for SPANS_SCHEMA."""

    def test_schema_is_valid(self):
        """Verify SPANS_SCHEMA is a valid PyArrow schema."""
        assert isinstance(SPANS_SCHEMA, pa.Schema)
        assert len(SPANS_SCHEMA) > 0

    def test_required_identity_fields(self):
        """Verify required identity fields are present and non-nullable."""
        assert SPANS_SCHEMA.field("trace_id").nullable is False
        assert SPANS_SCHEMA.field("span_id").nullable is False
        assert SPANS_SCHEMA.field("trace_id").type == pa.string()
        assert SPANS_SCHEMA.field("span_id").type == pa.string()

    def test_timing_fields(self):
        """Verify timing fields use int64 for nanoseconds."""
        assert SPANS_SCHEMA.field("start_time_unix_nano").type == pa.int64()
        assert SPANS_SCHEMA.field("end_time_unix_nano").type == pa.int64()
        assert SPANS_SCHEMA.field("duration_ns").type == pa.int64()

    def test_partition_columns(self):
        """Verify partition columns exist."""
        assert "date" in SPANS_SCHEMA.names
        assert "hour" in SPANS_SCHEMA.names
        assert SPANS_SCHEMA.field("date").type == pa.date32()
        assert SPANS_SCHEMA.field("hour").type == pa.int8()

    def test_http_attribute_columns(self):
        """Verify HTTP attribute columns for predicate pushdown."""
        http_cols = ["http_method", "http_status_code", "http_url", "http_route"]
        for col in http_cols:
            assert col in SPANS_SCHEMA.names

    def test_db_attribute_columns(self):
        """Verify database attribute columns."""
        db_cols = ["db_system", "db_name", "db_operation", "db_statement"]
        for col in db_cols:
            assert col in SPANS_SCHEMA.names

    def test_schema_metadata(self):
        """Verify schema includes metadata."""
        metadata = SPANS_SCHEMA.metadata
        assert metadata is not None
        assert b"otel_version" in metadata
        assert b"schema_version" in metadata


class TestMetricsSchema:
    """Tests for METRICS_SCHEMA."""

    def test_schema_is_valid(self):
        """Verify METRICS_SCHEMA is a valid PyArrow schema."""
        assert isinstance(METRICS_SCHEMA, pa.Schema)

    def test_metric_identity_fields(self):
        """Verify metric identity fields."""
        assert "metric_name" in METRICS_SCHEMA.names
        assert METRICS_SCHEMA.field("metric_name").nullable is False

    def test_histogram_fields(self):
        """Verify histogram fields exist."""
        histogram_cols = [
            "histogram_count",
            "histogram_sum",
            "histogram_bucket_counts",
            "histogram_explicit_bounds",
        ]
        for col in histogram_cols:
            assert col in METRICS_SCHEMA.names

    def test_value_fields(self):
        """Verify value fields for different metric types."""
        assert "value_double" in METRICS_SCHEMA.names
        assert "value_int" in METRICS_SCHEMA.names
        assert METRICS_SCHEMA.field("value_double").type == pa.float64()
        assert METRICS_SCHEMA.field("value_int").type == pa.int64()


class TestLogsSchema:
    """Tests for LOGS_SCHEMA."""

    def test_schema_is_valid(self):
        """Verify LOGS_SCHEMA is a valid PyArrow schema."""
        assert isinstance(LOGS_SCHEMA, pa.Schema)

    def test_timing_field(self):
        """Verify timing field."""
        assert LOGS_SCHEMA.field("time_unix_nano").type == pa.int64()
        assert LOGS_SCHEMA.field("time_unix_nano").nullable is False

    def test_severity_fields(self):
        """Verify severity fields."""
        assert "severity_number" in LOGS_SCHEMA.names
        assert "severity_text" in LOGS_SCHEMA.names
        assert LOGS_SCHEMA.field("severity_number").type == pa.int8()

    def test_trace_correlation(self):
        """Verify trace correlation fields."""
        assert "trace_id" in LOGS_SCHEMA.names
        assert "span_id" in LOGS_SCHEMA.names


class TestEnums:
    """Tests for enum types."""

    def test_span_kind_values(self):
        """Verify SpanKind enum values."""
        assert SpanKind.UNSPECIFIED == 0
        assert SpanKind.INTERNAL == 1
        assert SpanKind.SERVER == 2
        assert SpanKind.CLIENT == 3
        assert SpanKind.PRODUCER == 4
        assert SpanKind.CONSUMER == 5

    def test_status_code_values(self):
        """Verify StatusCode enum values."""
        assert StatusCode.UNSET == 0
        assert StatusCode.OK == 1
        assert StatusCode.ERROR == 2

    def test_severity_number_values(self):
        """Verify SeverityNumber enum values."""
        assert SeverityNumber.DEBUG == 5
        assert SeverityNumber.INFO == 9
        assert SeverityNumber.WARN == 13
        assert SeverityNumber.ERROR == 17
        assert SeverityNumber.FATAL == 21


class TestHelperFunctions:
    """Tests for schema helper functions."""

    def test_get_partition_columns(self):
        """Test get_partition_columns function."""
        partitions = get_partition_columns(SPANS_SCHEMA)
        assert "date" in partitions
        assert "hour" in partitions

    def test_get_required_columns(self):
        """Test get_required_columns function."""
        required = get_required_columns(SPANS_SCHEMA)
        assert "trace_id" in required
        assert "span_id" in required
        assert "parent_span_id" not in required  # nullable

    def test_span_kind_to_string(self):
        """Test span_kind_to_string conversion."""
        assert span_kind_to_string(2) == "SERVER"
        assert span_kind_to_string(3) == "CLIENT"

    def test_status_code_to_string(self):
        """Test status_code_to_string conversion."""
        assert status_code_to_string(1) == "OK"
        assert status_code_to_string(2) == "ERROR"

    def test_severity_to_string(self):
        """Test severity_to_string conversion."""
        assert severity_to_string(9) == "INFO"
        assert severity_to_string(17) == "ERROR"
        assert severity_to_string(100) == "SEVERITY_100"


class TestSpanEventsSchema:
    """Tests for SPAN_EVENTS_SCHEMA."""

    def test_schema_is_valid(self):
        """Verify SPAN_EVENTS_SCHEMA is valid."""
        assert isinstance(SPAN_EVENTS_SCHEMA, pa.Schema)
        assert "event_name" in SPAN_EVENTS_SCHEMA.names
        assert "time_unix_nano" in SPAN_EVENTS_SCHEMA.names


class TestSpanLinksSchema:
    """Tests for SPAN_LINKS_SCHEMA."""

    def test_schema_is_valid(self):
        """Verify SPAN_LINKS_SCHEMA is valid."""
        assert isinstance(SPAN_LINKS_SCHEMA, pa.Schema)
        assert "linked_trace_id" in SPAN_LINKS_SCHEMA.names
        assert "linked_span_id" in SPAN_LINKS_SCHEMA.names
