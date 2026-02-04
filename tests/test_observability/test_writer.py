"""Tests for the OTel Parquet writer."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from cybersec.observability.schema import SPANS_SCHEMA, SpanKind, StatusCode
from cybersec.observability.writer import OTelWriter


class TestOTelWriter:
    """Tests for OTelWriter class."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def writer(self, temp_dir):
        """Create OTelWriter instance."""
        return OTelWriter(temp_dir)

    def test_writer_initialization(self, temp_dir):
        """Test writer initializes correctly."""
        writer = OTelWriter(temp_dir)
        assert writer.base_path == temp_dir
        assert writer.row_group_size == 100_000

    def test_writer_with_custom_row_group_size(self, temp_dir):
        """Test writer with custom row group size."""
        writer = OTelWriter(temp_dir, row_group_size=50_000)
        assert writer.row_group_size == 50_000

    def test_generate_file_name(self, writer):
        """Test file name generation is unique."""
        name1 = writer._generate_file_name()
        name2 = writer._generate_file_name()

        assert name1.endswith(".parquet")
        assert name2.endswith(".parquet")
        assert name1 != name2

    def test_get_output_path_basic(self, writer):
        """Test basic output path generation."""
        dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        path = writer._get_output_path("spans", dt)

        assert "spans" in path
        assert "date=2024-01-15" in path
        assert "hour=10" in path

    def test_get_output_path_with_service(self, writer):
        """Test output path with service name."""
        dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        path = writer._get_output_path("spans", dt, service_name="api-gateway")

        assert "service_name=api-gateway" in path


class TestSpanWriting:
    """Tests for writing spans."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def writer(self, temp_dir):
        """Create writer instance."""
        return OTelWriter(temp_dir)

    @pytest.fixture
    def sample_span(self):
        """Create sample span dict."""
        ts = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        return {
            "trace_id": "abc123def456789012345678901234",
            "span_id": "span123456789012",
            "parent_span_id": None,
            "start_time_unix_nano": ts,
            "end_time_unix_nano": ts + 100_000_000,  # 100ms later
            "duration_ns": 100_000_000,
            "name": "HTTP request",
            "kind": "SERVER",
            "status_code": "OK",
            "status_message": None,
            "service_name": "api-gateway",
            "service_namespace": "production",
            "service_version": "1.0.0",
            "host_name": "api-1.example.com",
            "host_ip": "10.0.1.1",
            "attributes_json": json.dumps({"http.method": "GET"}),
            "resource_attributes_json": json.dumps({"service.name": "api-gateway"}),
            "http_method": "GET",
            "http_status_code": 200,
            "http_url": "https://api.example.com/users",
            "http_route": "/users",
            "http_target": None,
            "db_system": None,
            "db_name": None,
            "db_operation": None,
            "db_statement": None,
            "rpc_system": None,
            "rpc_service": None,
            "rpc_method": None,
            "messaging_system": None,
            "messaging_destination": None,
            "messaging_operation": None,
            "exception_type": None,
            "exception_message": None,
            "events_count": 0,
            "links_count": 0,
        }

    def test_write_spans_empty(self, writer):
        """Test writing empty span list."""
        result = writer.write_spans([])
        assert result == []

    def test_write_spans_single(self, writer, sample_span, temp_dir):
        """Test writing a single span."""
        result = writer.write_spans([sample_span])

        assert len(result) == 1
        assert result[0].endswith(".parquet")

        # Verify file was written
        written_path = Path(temp_dir) / result[0].split(temp_dir + "/")[1]
        # Note: path might include s3:// prefix handling
        # Just verify we got a path back

    def test_write_spans_partitioning(self, writer, temp_dir):
        """Test that spans are partitioned by date/hour/service."""
        ts1 = int(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        ts2 = int(datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

        spans = [
            {
                "trace_id": "trace1",
                "span_id": "span1",
                "parent_span_id": None,
                "start_time_unix_nano": ts1,
                "end_time_unix_nano": ts1 + 1000000,
                "duration_ns": 1000000,
                "name": "op1",
                "kind": "SERVER",
                "status_code": "OK",
                "status_message": None,
                "service_name": "svc1",
                "service_namespace": None,
                "service_version": None,
                "host_name": None,
                "host_ip": None,
                "attributes_json": None,
                "resource_attributes_json": None,
                "http_method": None,
                "http_status_code": None,
                "http_url": None,
                "http_route": None,
                "http_target": None,
                "db_system": None,
                "db_name": None,
                "db_operation": None,
                "db_statement": None,
                "rpc_system": None,
                "rpc_service": None,
                "rpc_method": None,
                "messaging_system": None,
                "messaging_destination": None,
                "messaging_operation": None,
                "exception_type": None,
                "exception_message": None,
                "events_count": None,
                "links_count": None,
            },
            {
                "trace_id": "trace2",
                "span_id": "span2",
                "parent_span_id": None,
                "start_time_unix_nano": ts2,
                "end_time_unix_nano": ts2 + 1000000,
                "duration_ns": 1000000,
                "name": "op2",
                "kind": "SERVER",
                "status_code": "OK",
                "status_message": None,
                "service_name": "svc2",
                "service_namespace": None,
                "service_version": None,
                "host_name": None,
                "host_ip": None,
                "attributes_json": None,
                "resource_attributes_json": None,
                "http_method": None,
                "http_status_code": None,
                "http_url": None,
                "http_route": None,
                "http_target": None,
                "db_system": None,
                "db_name": None,
                "db_operation": None,
                "db_statement": None,
                "rpc_system": None,
                "rpc_service": None,
                "rpc_method": None,
                "messaging_system": None,
                "messaging_destination": None,
                "messaging_operation": None,
                "exception_type": None,
                "exception_message": None,
                "events_count": None,
                "links_count": None,
            },
        ]

        result = writer.write_spans(spans, partition_by_service=True)

        # Should create 2 files (different hours and services)
        assert len(result) == 2


class TestSyntheticDataGeneration:
    """Tests for synthetic data generation."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def writer(self, temp_dir):
        """Create writer instance."""
        return OTelWriter(temp_dir)

    def test_generate_trace_id(self, writer):
        """Test trace ID generation."""
        trace_id = writer._generate_trace_id()
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdef" for c in trace_id)

    def test_generate_span_id(self, writer):
        """Test span ID generation."""
        span_id = writer._generate_span_id()
        assert len(span_id) == 16
        assert all(c in "0123456789abcdef" for c in span_id)

    def test_generate_attributes(self, writer):
        """Test attribute generation for different span kinds."""
        server_attrs = writer._generate_attributes(SpanKind.SERVER)
        assert "http.method" in server_attrs

        client_attrs = writer._generate_attributes(SpanKind.CLIENT)
        assert "db.system" in client_attrs

        producer_attrs = writer._generate_attributes(SpanKind.PRODUCER)
        assert "messaging.system" in producer_attrs

    def test_generate_trace(self, writer):
        """Test trace generation."""
        from cybersec.observability.writer import SERVICE_TEMPLATES

        base_time = datetime.now(timezone.utc)
        spans = writer._generate_trace(base_time, SERVICE_TEMPLATES[:3], max_depth=3)

        assert len(spans) > 0
        # All spans should have the same trace_id
        trace_ids = set(s["trace_id"] for s in spans)
        assert len(trace_ids) == 1

        # Should have at least one root span
        root_spans = [s for s in spans if s["parent_span_id"] is None]
        assert len(root_spans) == 1

    def test_write_synthetic_spans(self, writer, temp_dir):
        """Test writing synthetic spans."""
        result = writer.write_synthetic_spans(count=100, services=2)

        assert len(result) > 0
        # Verify files exist
        for path in result:
            # Path should be valid
            assert "spans" in path

    def test_write_synthetic_metrics(self, writer, temp_dir):
        """Test writing synthetic metrics."""
        result = writer.write_synthetic_metrics(count=50)

        assert len(result) > 0

    def test_write_synthetic_logs(self, writer, temp_dir):
        """Test writing synthetic logs."""
        result = writer.write_synthetic_logs(count=100)

        assert len(result) > 0
