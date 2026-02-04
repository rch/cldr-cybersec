"""Common data transformations for OTel data processing.

This module provides transformation functions for working with OpenTelemetry
data in Dask DataFrames, including timestamp conversion, attribute extraction,
and aggregation helpers.

Example:
    from cybersec.observability import OTelDataset
    from cybersec.observability.transforms import (
        to_datetime,
        extract_attribute,
        compute_service_metrics,
    )

    dataset = OTelDataset("s3://cybersec/otel/")
    ddf = dataset.load_spans(...)

    # Convert nanoseconds to datetime
    ddf["start_time"] = to_datetime(ddf["start_time_unix_nano"])

    # Extract specific attribute from JSON
    ddf["user_id"] = extract_attribute(ddf["attributes_json"], "user.id")
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import dask.dataframe as dd


# -----------------------------------------------------------------------------
# Timestamp Transformations
# -----------------------------------------------------------------------------

def to_datetime(ns_column: pd.Series) -> pd.Series:
    """Convert nanosecond timestamps to pandas datetime.

    Args:
        ns_column: Series of int64 nanosecond timestamps

    Returns:
        Series of datetime64[ns] values
    """
    return pd.to_datetime(ns_column, unit="ns", utc=True)


def to_unix_nano(dt_column: pd.Series) -> pd.Series:
    """Convert pandas datetime to nanosecond timestamps.

    Args:
        dt_column: Series of datetime values

    Returns:
        Series of int64 nanosecond timestamps
    """
    return (dt_column.astype("int64")).astype("int64")


def duration_to_ms(ns_column: pd.Series) -> pd.Series:
    """Convert nanosecond duration to milliseconds.

    Args:
        ns_column: Series of int64 nanosecond durations

    Returns:
        Series of float64 millisecond durations
    """
    return ns_column / 1_000_000


def duration_to_seconds(ns_column: pd.Series) -> pd.Series:
    """Convert nanosecond duration to seconds.

    Args:
        ns_column: Series of int64 nanosecond durations

    Returns:
        Series of float64 second durations
    """
    return ns_column / 1_000_000_000


# -----------------------------------------------------------------------------
# Attribute Extraction
# -----------------------------------------------------------------------------

def extract_attribute(json_column: pd.Series, key: str, default: Any = None) -> pd.Series:
    """Extract a single attribute from JSON attribute column.

    Args:
        json_column: Series of JSON strings
        key: Attribute key to extract (supports dot notation: "http.method")
        default: Default value if key not found

    Returns:
        Series of extracted values
    """
    def _extract(json_str: str | None) -> Any:
        if json_str is None:
            return default
        try:
            attrs = json.loads(json_str)
            # Support dot notation (e.g., "http.method")
            if "." in key and key not in attrs:
                # Try nested lookup
                parts = key.split(".")
                value = attrs
                for part in parts:
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        return default
                return value if value is not None else default
            return attrs.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    return json_column.apply(_extract)


def extract_attributes(
    json_column: pd.Series,
    keys: list[str],
    prefix: str = "",
) -> pd.DataFrame:
    """Extract multiple attributes from JSON column into separate columns.

    Args:
        json_column: Series of JSON strings
        keys: List of attribute keys to extract
        prefix: Optional prefix for output column names

    Returns:
        DataFrame with one column per extracted key
    """
    def _extract_all(json_str: str | None) -> dict[str, Any]:
        result = {key: None for key in keys}
        if json_str is None:
            return result
        try:
            attrs = json.loads(json_str)
            for key in keys:
                if key in attrs:
                    result[key] = attrs[key]
                elif "." in key:
                    # Try nested lookup
                    parts = key.split(".")
                    value = attrs
                    for part in parts:
                        if isinstance(value, dict):
                            value = value.get(part)
                        else:
                            value = None
                            break
                    result[key] = value
        except (json.JSONDecodeError, TypeError):
            pass
        return result

    extracted = json_column.apply(_extract_all).apply(pd.Series)
    if prefix:
        extracted.columns = [f"{prefix}{col}" for col in extracted.columns]
    return extracted


def parse_attributes(json_column: pd.Series) -> pd.Series:
    """Parse JSON attributes to Python dictionaries.

    Args:
        json_column: Series of JSON strings

    Returns:
        Series of dictionaries
    """
    def _parse(json_str: str | None) -> dict[str, Any]:
        if json_str is None:
            return {}
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return {}

    return json_column.apply(_parse)


# -----------------------------------------------------------------------------
# Span Transformations
# -----------------------------------------------------------------------------

def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add commonly used derived columns to spans DataFrame.

    Adds:
        - start_time: datetime from start_time_unix_nano
        - end_time: datetime from end_time_unix_nano
        - duration_ms: duration in milliseconds
        - is_error: boolean for error status

    Args:
        df: DataFrame with span columns

    Returns:
        DataFrame with additional derived columns
    """
    result = df.copy()

    if "start_time_unix_nano" in df.columns:
        result["start_time"] = to_datetime(df["start_time_unix_nano"])

    if "end_time_unix_nano" in df.columns:
        result["end_time"] = to_datetime(df["end_time_unix_nano"])

    if "duration_ns" in df.columns:
        result["duration_ms"] = duration_to_ms(df["duration_ns"])

    if "status_code" in df.columns:
        result["is_error"] = df["status_code"] == "ERROR"

    return result


def classify_span_type(df: pd.DataFrame) -> pd.Series:
    """Classify spans by their semantic type based on attributes.

    Returns one of: http, database, messaging, rpc, internal, unknown

    Args:
        df: DataFrame with span attribute columns

    Returns:
        Series of span type classifications
    """
    def _classify(row: pd.Series) -> str:
        if pd.notna(row.get("http_method")):
            return "http"
        if pd.notna(row.get("db_system")):
            return "database"
        if pd.notna(row.get("messaging_system")):
            return "messaging"
        if pd.notna(row.get("rpc_system")):
            return "rpc"
        if row.get("kind") == "INTERNAL":
            return "internal"
        return "unknown"

    return df.apply(_classify, axis=1)


def is_root_span(df: pd.DataFrame) -> pd.Series:
    """Identify root spans (no parent).

    Args:
        df: DataFrame with parent_span_id column

    Returns:
        Boolean series indicating root spans
    """
    return df["parent_span_id"].isna() | (df["parent_span_id"] == "")


def calculate_self_time(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate self-time for each span (exclusive of child duration).

    Self-time = span duration - sum of direct child durations

    Args:
        df: DataFrame with spans from a single trace

    Returns:
        DataFrame with added 'self_time_ns' column
    """
    result = df.copy()

    # Group children by parent
    child_durations = df.groupby("parent_span_id")["duration_ns"].sum()

    # Calculate self time
    result["self_time_ns"] = df.apply(
        lambda row: row["duration_ns"] - child_durations.get(row["span_id"], 0),
        axis=1,
    )
    # Ensure non-negative (can happen with clock skew)
    result["self_time_ns"] = result["self_time_ns"].clip(lower=0)

    return result


# -----------------------------------------------------------------------------
# Aggregation Helpers
# -----------------------------------------------------------------------------

def compute_latency_percentiles(
    duration_series: pd.Series,
    percentiles: list[float] | None = None,
) -> dict[str, float]:
    """Compute latency percentiles from duration data.

    Args:
        duration_series: Series of duration values (any unit)
        percentiles: List of percentiles to compute (default: p50, p90, p95, p99)

    Returns:
        Dictionary mapping percentile names to values
    """
    if percentiles is None:
        percentiles = [0.5, 0.9, 0.95, 0.99]

    results = {}
    for p in percentiles:
        label = f"p{int(p * 100)}"
        results[label] = duration_series.quantile(p)

    results["mean"] = duration_series.mean()
    results["min"] = duration_series.min()
    results["max"] = duration_series.max()
    results["count"] = len(duration_series)

    return results


def compute_service_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute aggregated metrics per service.

    Computes:
        - span_count: number of spans
        - trace_count: number of unique traces
        - error_count: number of error spans
        - error_rate: fraction of error spans
        - avg_duration_ms: average duration
        - p50_duration_ms, p95_duration_ms, p99_duration_ms: latency percentiles

    Args:
        df: DataFrame with service_name, trace_id, status_code, duration_ns

    Returns:
        DataFrame with one row per service
    """
    return df.groupby("service_name").agg(
        span_count=("span_id", "count"),
        trace_count=("trace_id", "nunique"),
        error_count=("status_code", lambda x: (x == "ERROR").sum()),
        avg_duration_ms=("duration_ns", lambda x: x.mean() / 1_000_000),
        p50_duration_ms=("duration_ns", lambda x: x.quantile(0.5) / 1_000_000),
        p95_duration_ms=("duration_ns", lambda x: x.quantile(0.95) / 1_000_000),
        p99_duration_ms=("duration_ns", lambda x: x.quantile(0.99) / 1_000_000),
    ).assign(
        error_rate=lambda x: x["error_count"] / x["span_count"],
    ).reset_index()


def compute_operation_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute aggregated metrics per service/operation combination.

    Args:
        df: DataFrame with service_name, name (operation), status_code, duration_ns

    Returns:
        DataFrame with one row per service/operation
    """
    return df.groupby(["service_name", "name"]).agg(
        span_count=("span_id", "count"),
        error_count=("status_code", lambda x: (x == "ERROR").sum()),
        avg_duration_ms=("duration_ns", lambda x: x.mean() / 1_000_000),
        p50_duration_ms=("duration_ns", lambda x: x.quantile(0.5) / 1_000_000),
        p95_duration_ms=("duration_ns", lambda x: x.quantile(0.95) / 1_000_000),
        p99_duration_ms=("duration_ns", lambda x: x.quantile(0.99) / 1_000_000),
    ).assign(
        error_rate=lambda x: x["error_count"] / x["span_count"],
    ).reset_index()


def compute_time_series(
    df: pd.DataFrame,
    time_column: str = "start_time_unix_nano",
    interval: str = "1min",
    metrics: list[str] | None = None,
) -> pd.DataFrame:
    """Compute time-bucketed metrics.

    Args:
        df: DataFrame with timestamp and metric columns
        time_column: Name of timestamp column (nanoseconds)
        interval: Time bucket interval (pandas frequency string)
        metrics: List of metrics to compute (default: count, error_rate, avg_duration)

    Returns:
        DataFrame with time-bucketed metrics
    """
    # Convert to datetime for resampling
    df = df.copy()
    df["_time"] = to_datetime(df[time_column])
    df = df.set_index("_time")

    if metrics is None:
        metrics = ["count", "error_rate", "avg_duration_ms"]

    agg_dict = {}
    if "count" in metrics:
        agg_dict["count"] = ("span_id", "count")
    if "error_rate" in metrics or "error_count" in metrics:
        agg_dict["error_count"] = ("status_code", lambda x: (x == "ERROR").sum())
    if "avg_duration_ms" in metrics:
        agg_dict["avg_duration_ms"] = ("duration_ns", lambda x: x.mean() / 1_000_000)
    if "p99_duration_ms" in metrics:
        agg_dict["p99_duration_ms"] = ("duration_ns", lambda x: x.quantile(0.99) / 1_000_000)

    result = df.resample(interval).agg(**agg_dict)

    if "error_rate" in metrics and "count" in agg_dict:
        result["error_rate"] = result["error_count"] / result["count"]

    return result.reset_index().rename(columns={"_time": "time"})


# -----------------------------------------------------------------------------
# Trace Analysis
# -----------------------------------------------------------------------------

def build_trace_tree(df: pd.DataFrame) -> dict[str, Any]:
    """Build a tree structure from trace spans.

    Args:
        df: DataFrame with spans from a single trace

    Returns:
        Nested dictionary representing the trace tree
    """
    spans = df.to_dict("records")
    span_map = {s["span_id"]: s for s in spans}

    # Find root span
    root = None
    for span in spans:
        if pd.isna(span.get("parent_span_id")) or span.get("parent_span_id") == "":
            root = span
            break

    if root is None:
        # No root found, return flat structure
        return {"spans": spans, "root": None}

    def build_node(span: dict) -> dict:
        """Recursively build tree node."""
        children = [
            build_node(span_map[s["span_id"]])
            for s in spans
            if s.get("parent_span_id") == span["span_id"]
        ]
        return {
            **span,
            "children": children,
        }

    return build_node(root)


def get_critical_path(df: pd.DataFrame) -> pd.DataFrame:
    """Find the critical path (longest chain) through a trace.

    The critical path is the sequence of spans that determines the
    overall trace duration.

    Args:
        df: DataFrame with spans from a single trace

    Returns:
        DataFrame containing only critical path spans
    """
    spans = df.to_dict("records")
    span_map = {s["span_id"]: s for s in spans}

    # Build parent lookup
    children_map: dict[str | None, list[dict]] = {}
    for span in spans:
        parent = span.get("parent_span_id")
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(span)

    # Find critical path by following longest child at each level
    critical_path = []

    # Find root
    roots = children_map.get(None, []) + children_map.get("", [])
    if not roots:
        return df

    current = max(roots, key=lambda s: s["duration_ns"])
    critical_path.append(current)

    while current["span_id"] in children_map or any(
        s.get("parent_span_id") == current["span_id"] for s in spans
    ):
        children = [
            s for s in spans if s.get("parent_span_id") == current["span_id"]
        ]
        if not children:
            break
        current = max(children, key=lambda s: s["duration_ns"])
        critical_path.append(current)

    critical_ids = {s["span_id"] for s in critical_path}
    return df[df["span_id"].isin(critical_ids)]


# -----------------------------------------------------------------------------
# Service Dependency Analysis
# -----------------------------------------------------------------------------

def extract_service_dependencies(df: pd.DataFrame) -> pd.DataFrame:
    """Extract service-to-service call relationships from spans.

    Identifies caller/callee relationships based on parent spans.

    Args:
        df: DataFrame with service_name, span_id, parent_span_id columns

    Returns:
        DataFrame with columns: caller_service, callee_service, call_count
    """
    # Create span-to-service mapping
    span_service = df.set_index("span_id")["service_name"].to_dict()

    # Find cross-service calls
    edges = []
    for _, row in df.iterrows():
        parent_id = row.get("parent_span_id")
        if parent_id and parent_id in span_service:
            caller = span_service[parent_id]
            callee = row["service_name"]
            if caller != callee:  # Cross-service call
                edges.append({"caller_service": caller, "callee_service": callee})

    if not edges:
        return pd.DataFrame(columns=["caller_service", "callee_service", "call_count"])

    edges_df = pd.DataFrame(edges)
    return edges_df.groupby(
        ["caller_service", "callee_service"]
    ).size().reset_index(name="call_count")


def extract_service_metrics_by_caller(df: pd.DataFrame) -> pd.DataFrame:
    """Extract service metrics broken down by calling service.

    Useful for understanding how different callers affect service performance.

    Args:
        df: DataFrame with service_name, span_id, parent_span_id, duration_ns

    Returns:
        DataFrame with caller_service, callee_service, and latency metrics
    """
    span_service = df.set_index("span_id")["service_name"].to_dict()

    # Add caller service column
    df = df.copy()
    df["caller_service"] = df["parent_span_id"].map(span_service)

    # Filter to cross-service calls
    cross_service = df[df["caller_service"].notna() & (df["caller_service"] != df["service_name"])]

    if len(cross_service) == 0:
        return pd.DataFrame(columns=[
            "caller_service", "callee_service", "call_count",
            "avg_duration_ms", "p99_duration_ms", "error_rate"
        ])

    return cross_service.groupby(
        ["caller_service", "service_name"]
    ).agg(
        call_count=("span_id", "count"),
        avg_duration_ms=("duration_ns", lambda x: x.mean() / 1_000_000),
        p99_duration_ms=("duration_ns", lambda x: x.quantile(0.99) / 1_000_000),
        error_count=("status_code", lambda x: (x == "ERROR").sum()),
    ).assign(
        error_rate=lambda x: x["error_count"] / x["call_count"],
    ).reset_index().rename(columns={"service_name": "callee_service"})
