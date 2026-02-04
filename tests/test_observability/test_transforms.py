"""Tests for observability data transforms."""

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from cybersec.observability.transforms import (
    add_derived_columns,
    build_trace_tree,
    classify_span_type,
    compute_latency_percentiles,
    compute_operation_metrics,
    compute_service_metrics,
    duration_to_ms,
    duration_to_seconds,
    extract_attribute,
    extract_attributes,
    extract_service_dependencies,
    get_critical_path,
    is_root_span,
    parse_attributes,
    to_datetime,
)


class TestTimestampTransforms:
    """Tests for timestamp transformation functions."""

    def test_to_datetime(self):
        """Test nanosecond to datetime conversion."""
        # 2024-01-01 00:00:00 UTC in nanoseconds
        ns = 1704067200_000_000_000
        series = pd.Series([ns])
        result = to_datetime(series)

        assert len(result) == 1
        assert result.iloc[0].year == 2024
        assert result.iloc[0].month == 1
        assert result.iloc[0].day == 1

    def test_duration_to_ms(self):
        """Test nanosecond to millisecond conversion."""
        ns = pd.Series([1_000_000, 2_500_000, 10_000_000])
        result = duration_to_ms(ns)

        assert result.iloc[0] == 1.0
        assert result.iloc[1] == 2.5
        assert result.iloc[2] == 10.0

    def test_duration_to_seconds(self):
        """Test nanosecond to second conversion."""
        ns = pd.Series([1_000_000_000, 2_500_000_000])
        result = duration_to_seconds(ns)

        assert result.iloc[0] == 1.0
        assert result.iloc[1] == 2.5


class TestAttributeExtraction:
    """Tests for attribute extraction functions."""

    def test_extract_attribute_simple(self):
        """Test extracting a simple attribute."""
        attrs = pd.Series([
            json.dumps({"http.method": "GET", "http.status_code": 200}),
            json.dumps({"http.method": "POST", "http.status_code": 201}),
        ])
        result = extract_attribute(attrs, "http.method")

        assert result.iloc[0] == "GET"
        assert result.iloc[1] == "POST"

    def test_extract_attribute_missing(self):
        """Test extracting missing attribute returns default."""
        attrs = pd.Series([json.dumps({"http.method": "GET"})])
        result = extract_attribute(attrs, "db.system", default="unknown")

        assert result.iloc[0] == "unknown"

    def test_extract_attribute_null(self):
        """Test extracting from null returns default."""
        attrs = pd.Series([None, json.dumps({"key": "value"})])
        result = extract_attribute(attrs, "key", default="default")

        assert result.iloc[0] == "default"
        assert result.iloc[1] == "value"

    def test_extract_attributes_multiple(self):
        """Test extracting multiple attributes."""
        attrs = pd.Series([
            json.dumps({"http.method": "GET", "http.status_code": 200}),
        ])
        result = extract_attributes(attrs, ["http.method", "http.status_code"])

        assert "http.method" in result.columns
        assert "http.status_code" in result.columns
        assert result["http.method"].iloc[0] == "GET"
        assert result["http.status_code"].iloc[0] == 200

    def test_parse_attributes(self):
        """Test parsing JSON attributes to dicts."""
        attrs = pd.Series([
            json.dumps({"key": "value"}),
            None,
            "invalid json",
        ])
        result = parse_attributes(attrs)

        assert result.iloc[0] == {"key": "value"}
        assert result.iloc[1] == {}
        assert result.iloc[2] == {}


class TestSpanTransforms:
    """Tests for span transformation functions."""

    @pytest.fixture
    def sample_spans_df(self):
        """Create sample spans DataFrame for testing."""
        return pd.DataFrame({
            "span_id": ["span1", "span2", "span3"],
            "parent_span_id": [None, "span1", "span1"],
            "start_time_unix_nano": [
                1704067200_000_000_000,
                1704067200_100_000_000,
                1704067200_200_000_000,
            ],
            "end_time_unix_nano": [
                1704067200_500_000_000,
                1704067200_300_000_000,
                1704067200_400_000_000,
            ],
            "duration_ns": [500_000_000, 200_000_000, 200_000_000],
            "status_code": ["OK", "OK", "ERROR"],
            "service_name": ["api-gateway", "auth-service", "user-service"],
            "name": ["HTTP request", "validate_token", "get_user"],
            "kind": ["SERVER", "INTERNAL", "CLIENT"],
            "http_method": ["GET", None, None],
            "db_system": [None, None, "postgresql"],
            "messaging_system": [None, None, None],
            "rpc_system": [None, None, None],
        })

    def test_add_derived_columns(self, sample_spans_df):
        """Test adding derived columns."""
        result = add_derived_columns(sample_spans_df)

        assert "start_time" in result.columns
        assert "end_time" in result.columns
        assert "duration_ms" in result.columns
        assert "is_error" in result.columns

        assert result["duration_ms"].iloc[0] == 500.0
        assert result["is_error"].iloc[0] is False
        assert result["is_error"].iloc[2] is True

    def test_classify_span_type(self, sample_spans_df):
        """Test span type classification."""
        result = classify_span_type(sample_spans_df)

        assert result.iloc[0] == "http"  # has http_method
        assert result.iloc[2] == "database"  # has db_system

    def test_is_root_span(self, sample_spans_df):
        """Test root span identification."""
        result = is_root_span(sample_spans_df)

        assert result.iloc[0] is True  # no parent
        assert result.iloc[1] is False  # has parent
        assert result.iloc[2] is False  # has parent


class TestAggregationHelpers:
    """Tests for aggregation helper functions."""

    def test_compute_latency_percentiles(self):
        """Test latency percentile computation."""
        durations = pd.Series([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        result = compute_latency_percentiles(durations)

        assert "p50" in result
        assert "p99" in result
        assert "mean" in result
        assert "count" in result
        assert result["count"] == 10
        assert result["mean"] == 55.0

    def test_compute_service_metrics(self):
        """Test service metrics computation."""
        df = pd.DataFrame({
            "span_id": ["s1", "s2", "s3", "s4"],
            "trace_id": ["t1", "t1", "t2", "t2"],
            "service_name": ["svc1", "svc1", "svc2", "svc2"],
            "status_code": ["OK", "ERROR", "OK", "OK"],
            "duration_ns": [100_000_000, 200_000_000, 150_000_000, 250_000_000],
        })
        result = compute_service_metrics(df)

        assert len(result) == 2
        svc1 = result[result["service_name"] == "svc1"].iloc[0]
        assert svc1["span_count"] == 2
        assert svc1["error_count"] == 1
        assert svc1["error_rate"] == 0.5

    def test_compute_operation_metrics(self):
        """Test operation metrics computation."""
        df = pd.DataFrame({
            "span_id": ["s1", "s2", "s3"],
            "service_name": ["svc1", "svc1", "svc1"],
            "name": ["op1", "op1", "op2"],
            "status_code": ["OK", "OK", "ERROR"],
            "duration_ns": [100_000_000, 200_000_000, 150_000_000],
        })
        result = compute_operation_metrics(df)

        assert len(result) == 2
        op1 = result[(result["service_name"] == "svc1") & (result["name"] == "op1")].iloc[0]
        assert op1["span_count"] == 2


class TestTraceAnalysis:
    """Tests for trace analysis functions."""

    @pytest.fixture
    def trace_df(self):
        """Create trace DataFrame for testing."""
        return pd.DataFrame({
            "span_id": ["root", "child1", "child2", "grandchild"],
            "parent_span_id": [None, "root", "root", "child1"],
            "service_name": ["gateway", "auth", "user", "db"],
            "name": ["request", "validate", "get_user", "query"],
            "duration_ns": [1000, 300, 400, 200],
            "status_code": ["OK", "OK", "OK", "OK"],
        })

    def test_build_trace_tree(self, trace_df):
        """Test building trace tree structure."""
        tree = build_trace_tree(trace_df)

        assert tree["span_id"] == "root"
        assert len(tree["children"]) == 2

    def test_get_critical_path(self, trace_df):
        """Test finding critical path."""
        result = get_critical_path(trace_df)

        # Critical path should include root and one of its children
        assert "root" in result["span_id"].values


class TestServiceDependencyAnalysis:
    """Tests for service dependency analysis functions."""

    def test_extract_service_dependencies(self):
        """Test extracting service dependencies."""
        df = pd.DataFrame({
            "span_id": ["s1", "s2", "s3", "s4"],
            "parent_span_id": [None, "s1", "s1", "s2"],
            "service_name": ["gateway", "auth", "user", "db"],
        })
        result = extract_service_dependencies(df)

        # Should have 3 edges: gateway->auth, gateway->user, auth->db
        assert len(result) == 3
        assert "caller_service" in result.columns
        assert "callee_service" in result.columns
        assert "call_count" in result.columns

    def test_extract_service_dependencies_same_service(self):
        """Test that same-service calls are excluded."""
        df = pd.DataFrame({
            "span_id": ["s1", "s2"],
            "parent_span_id": [None, "s1"],
            "service_name": ["svc1", "svc1"],  # Same service
        })
        result = extract_service_dependencies(df)

        assert len(result) == 0
