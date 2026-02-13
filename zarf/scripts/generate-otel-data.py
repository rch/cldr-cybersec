#!/usr/bin/env python3
"""
OTEL Synthetic Data Generator

Generate synthetic OpenTelemetry span data for testing Dask out-of-core processing.
Writes Parquet files partitioned by date/hour to S3-compatible storage.

Usage:
    # Minimal dataset (~50MB, 50K spans) for validation
    uv run python zarf/scripts/generate-otel-data.py --mode minimal

    # Large dataset (~1.3TB, 15B spans) for stress testing
    uv run python zarf/scripts/generate-otel-data.py --mode large --bucket my-bucket

    # Custom configuration
    uv run python zarf/scripts/generate-otel-data.py \
        --spans 1000000 \
        --services 10 \
        --duration-hours 168 \
        --bucket cybersec-dask-data \
        --prefix otel-custom

Environment Variables:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION - AWS credentials
    S3_ENDPOINT - Custom S3 endpoint (e.g., http://localhost:9010 for MinIO)
"""

import argparse
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import fs
except ImportError:
    print("ERROR: pyarrow is required. Install with: pip install pyarrow")
    sys.exit(1)


# =============================================================================
# Schema Definition (matches Ansible datagen role)
# =============================================================================

_span_kind_dict = pa.dictionary(pa.int8(), pa.string())
_status_code_dict = pa.dictionary(pa.int8(), pa.string())

SPANS_SCHEMA = pa.schema([
    pa.field("trace_id", pa.string(), nullable=False),
    pa.field("span_id", pa.string(), nullable=False),
    pa.field("parent_span_id", pa.string(), nullable=True),
    pa.field("start_time_unix_nano", pa.int64(), nullable=False),
    pa.field("end_time_unix_nano", pa.int64(), nullable=False),
    pa.field("duration_ns", pa.int64(), nullable=False),
    pa.field("name", pa.string(), nullable=False),
    pa.field("kind", _span_kind_dict, nullable=False),
    pa.field("status_code", _status_code_dict, nullable=False),
    pa.field("status_message", pa.string(), nullable=True),
    pa.field("service_name", pa.string(), nullable=False),
    pa.field("service_namespace", pa.string(), nullable=True),
    pa.field("service_version", pa.string(), nullable=True),
    pa.field("host_name", pa.string(), nullable=True),
    pa.field("host_ip", pa.string(), nullable=True),
    pa.field("attributes_json", pa.string(), nullable=True),
    pa.field("resource_attributes_json", pa.string(), nullable=True),
    pa.field("http_method", pa.string(), nullable=True),
    pa.field("http_status_code", pa.int16(), nullable=True),
    pa.field("http_url", pa.string(), nullable=True),
    pa.field("http_route", pa.string(), nullable=True),
    pa.field("http_target", pa.string(), nullable=True),
    pa.field("db_system", pa.string(), nullable=True),
    pa.field("db_name", pa.string(), nullable=True),
    pa.field("db_operation", pa.string(), nullable=True),
    pa.field("db_statement", pa.string(), nullable=True),
    pa.field("rpc_system", pa.string(), nullable=True),
    pa.field("rpc_service", pa.string(), nullable=True),
    pa.field("rpc_method", pa.string(), nullable=True),
    pa.field("messaging_system", pa.string(), nullable=True),
    pa.field("messaging_destination", pa.string(), nullable=True),
    pa.field("messaging_operation", pa.string(), nullable=True),
    pa.field("exception_type", pa.string(), nullable=True),
    pa.field("exception_message", pa.string(), nullable=True),
    pa.field("events_count", pa.int32(), nullable=True),
    pa.field("links_count", pa.int32(), nullable=True),
])

# =============================================================================
# Service Templates
# =============================================================================

SERVICE_TEMPLATES = [
    {"name": "api-gateway", "kind": "SERVER", "operations": ["HTTP request", "route", "proxy"]},
    {"name": "auth-service", "kind": "SERVER", "operations": ["validate_token", "login", "logout", "refresh_token"]},
    {"name": "user-service", "kind": "SERVER", "operations": ["get_user", "update_user", "list_users", "delete_user"]},
    {"name": "order-service", "kind": "SERVER", "operations": ["create_order", "get_order", "update_order", "cancel_order"]},
    {"name": "inventory-service", "kind": "SERVER", "operations": ["check_stock", "reserve", "release", "update_quantity"]},
    {"name": "payment-service", "kind": "SERVER", "operations": ["process_payment", "refund", "verify"]},
    {"name": "notification-service", "kind": "SERVER", "operations": ["send_email", "send_sms", "send_push"]},
    {"name": "search-service", "kind": "SERVER", "operations": ["search", "index", "suggest"]},
    {"name": "cache-service", "kind": "CLIENT", "operations": ["GET", "SET", "DEL", "EXPIRE"]},
    {"name": "database", "kind": "CLIENT", "operations": ["SELECT", "INSERT", "UPDATE", "DELETE"]},
]

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
HTTP_ROUTES = [
    "/api/v1/users", "/api/v1/users/{id}", "/api/v1/orders", "/api/v1/orders/{id}",
    "/api/v1/products", "/api/v1/products/{id}", "/api/v1/auth/login", "/api/v1/auth/logout",
    "/api/v1/search", "/api/v1/cart", "/api/v1/checkout", "/health", "/metrics"
]
HTTP_STATUS_CODES = [200, 200, 200, 200, 201, 204, 400, 401, 403, 404, 500, 502, 503]


def generate_span(
    service: dict[str, Any],
    start_ns: int,
    time_range_ns: int,
) -> dict[str, Any]:
    """Generate a single synthetic span."""
    offset_ns = random.randint(0, time_range_ns)
    span_start_ns = start_ns + offset_ns

    # Exponential duration distribution (median ~50ms)
    duration_ns = int(random.expovariate(1 / 50_000_000))
    duration_ns = max(1_000_000, min(duration_ns, 30_000_000_000))  # 1ms to 30s
    span_end_ns = span_start_ns + duration_ns

    operation = random.choice(service["operations"])
    is_error = random.random() < 0.05  # 5% error rate

    is_http = service["kind"] == "SERVER"
    http_method = random.choice(HTTP_METHODS) if is_http else None
    http_route = random.choice(HTTP_ROUTES) if is_http else None
    http_status = random.choice(HTTP_STATUS_CODES) if is_http else None

    is_db = service["name"] in ("database", "cache-service")

    dt = datetime.fromtimestamp(span_start_ns / 1_000_000_000, tz=timezone.utc)

    return {
        "trace_id": uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
        "parent_span_id": None,
        "start_time_unix_nano": span_start_ns,
        "end_time_unix_nano": span_end_ns,
        "duration_ns": duration_ns,
        "name": operation,
        "kind": service["kind"],
        "status_code": "ERROR" if is_error else "OK",
        "status_message": "Error occurred" if is_error else None,
        "service_name": service["name"],
        "service_namespace": "production",
        "service_version": f"1.{random.randint(0, 9)}.{random.randint(0, 99)}",
        "host_name": f"{service['name']}-{random.randint(1, 10)}.cluster.local",
        "host_ip": f"10.{random.randint(0, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
        "attributes_json": json.dumps({"service.name": service["name"], "deployment.environment": "production"}),
        "resource_attributes_json": json.dumps({"k8s.namespace.name": "default", "k8s.pod.name": f"{service['name']}-{uuid.uuid4().hex[:8]}"}),
        "http_method": http_method,
        "http_status_code": http_status,
        "http_url": f"https://api.example.com{http_route}" if http_route else None,
        "http_route": http_route,
        "http_target": http_route.replace("{id}", str(random.randint(1, 10000))) if http_route and "{id}" in http_route else http_route,
        "db_system": "postgresql" if service["name"] == "database" else ("redis" if is_db else None),
        "db_name": "production" if is_db else None,
        "db_operation": operation if is_db else None,
        "db_statement": f"{operation} FROM {random.choice(['users', 'orders', 'products'])}" if service["name"] == "database" else None,
        "rpc_system": None,
        "rpc_service": None,
        "rpc_method": None,
        "messaging_system": "kafka" if random.random() < 0.1 else None,
        "messaging_destination": f"topic-{random.choice(['orders', 'events', 'notifications'])}" if random.random() < 0.1 else None,
        "messaging_operation": random.choice(["publish", "receive"]) if random.random() < 0.1 else None,
        "exception_type": random.choice(["RuntimeError", "ValueError", "TimeoutError", "ConnectionError"]) if is_error else None,
        "exception_message": random.choice(["Connection refused", "Timeout exceeded", "Invalid input", "Service unavailable"]) if is_error else None,
        "events_count": random.randint(0, 5),
        "links_count": random.randint(0, 2) if random.random() < 0.1 else 0,
        "_date": dt.date(),
        "_hour": dt.hour,
    }


def generate_batch(
    batch_size: int,
    services: list[dict[str, Any]],
    start_ns: int,
    time_range_ns: int,
) -> list[dict[str, Any]]:
    """Generate a batch of spans."""
    return [
        generate_span(random.choice(services), start_ns, time_range_ns)
        for _ in range(batch_size)
    ]


def write_to_s3(
    spans: list[dict[str, Any]],
    s3_fs: fs.S3FileSystem,
    base_path: str,
    file_prefix: str,
) -> tuple[int, int]:
    """Write spans to S3 partitioned by date/hour. Returns (files_written, total_bytes)."""
    # Group by partition
    partitions: dict[tuple, list[dict]] = {}
    for span in spans:
        key = (span.pop("_date"), span.pop("_hour"))
        partitions.setdefault(key, []).append(span)

    files_written = 0
    total_bytes = 0

    for (date_val, hour_val), partition_spans in partitions.items():
        output_dir = f"{base_path}/spans/date={date_val.isoformat()}/hour={hour_val:02d}"
        try:
            s3_fs.create_dir(output_dir)
        except Exception:
            pass

        file_path = f"{output_dir}/{file_prefix}_{uuid.uuid4().hex[:8]}.parquet"
        table = pa.Table.from_pylist(partition_spans, schema=SPANS_SCHEMA)
        pq.write_table(table, file_path, filesystem=s3_fs, compression="snappy")

        try:
            info = s3_fs.get_file_info(file_path)
            total_bytes += info.size
        except Exception:
            pass
        files_written += 1

    return files_written, total_bytes


def write_active_dataset_marker(
    s3_fs: fs.S3FileSystem,
    bucket: str,
    prefix: str,
    span_count: int,
    phase: str,
) -> None:
    """Write _active_dataset.json marker file."""
    marker = {
        "dataset": prefix,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "span_count": span_count,
    }
    marker_path = f"{bucket}/_active_dataset.json"
    with s3_fs.open_output_stream(marker_path) as f:
        f.write(json.dumps(marker, indent=2).encode())
    print(f"Wrote marker: s3://{marker_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic OTEL span data for Dask testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--mode",
        choices=["minimal", "large", "custom"],
        default="minimal",
        help="Generation mode: minimal (~50MB), large (~1.3TB), or custom",
    )
    parser.add_argument(
        "--spans",
        type=int,
        default=None,
        help="Number of spans to generate (overrides mode default)",
    )
    parser.add_argument(
        "--services",
        type=int,
        default=None,
        help="Number of service types (1-10, default: 5 for minimal, 10 for large)",
    )
    parser.add_argument(
        "--duration-hours",
        type=int,
        default=None,
        help="Time range for spans in hours (default: 24 for minimal, 168 for large)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("OTEL_S3_BUCKET", "cybersec-dask-data"),
        help="S3 bucket name (default: $OTEL_S3_BUCKET or cybersec-dask-data)",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="S3 prefix for output (default: otel-minimal or otel-large)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region (default: $AWS_REGION or us-east-1)",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("S3_ENDPOINT"),
        help="Custom S3 endpoint URL (e.g., http://localhost:9010 for MinIO)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100000,
        help="Batch size for generation (default: 100000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print configuration and exit without generating data",
    )

    args = parser.parse_args()

    # Mode defaults
    mode_config = {
        "minimal": {"spans": 50000, "services": 5, "duration_hours": 24, "prefix": "otel-minimal"},
        "large": {"spans": 15_000_000_000, "services": 10, "duration_hours": 168, "prefix": "otel-large"},
        "custom": {"spans": 100000, "services": 5, "duration_hours": 24, "prefix": "otel-custom"},
    }

    config = mode_config[args.mode]

    # Override with CLI args
    span_count = args.spans or config["spans"]
    num_services = args.services or config["services"]
    duration_hours = args.duration_hours or config["duration_hours"]
    prefix = args.prefix or config["prefix"]

    services = SERVICE_TEMPLATES[:num_services]

    print("=" * 70)
    print("OTEL Synthetic Data Generator")
    print("=" * 70)
    print(f"Mode:           {args.mode}")
    print(f"Spans:          {span_count:,}")
    print(f"Services:       {num_services}")
    print(f"Duration:       {duration_hours} hours")
    print(f"Bucket:         {args.bucket}")
    print(f"Prefix:         {prefix}")
    print(f"Region:         {args.region}")
    print(f"Endpoint:       {args.endpoint or '(AWS default)'}")
    print(f"Batch size:     {args.batch_size:,}")
    estimated_size_mb = span_count * 0.001  # ~1KB per span
    print(f"Estimated size: {estimated_size_mb / 1024:.1f} GiB")
    print("=" * 70)

    if args.dry_run:
        print("Dry run - exiting without generating data")
        return 0

    # Check AWS credentials
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        print("ERROR: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set")
        return 1

    # Initialize S3
    s3_kwargs = {
        "access_key": os.environ["AWS_ACCESS_KEY_ID"],
        "secret_key": os.environ["AWS_SECRET_ACCESS_KEY"],
        "region": args.region,
    }
    if args.endpoint:
        s3_kwargs["endpoint_override"] = args.endpoint
        s3_kwargs["scheme"] = "http" if args.endpoint.startswith("http://") else "https"

    s3 = fs.S3FileSystem(**s3_kwargs)
    base_path = f"{args.bucket}/{prefix}"

    # Time range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=duration_hours)
    time_range_ns = duration_hours * 3600 * 1_000_000_000
    start_ns = int(start_time.timestamp() * 1_000_000_000)

    print(f"Time range: {start_time.isoformat()} to {end_time.isoformat()}")
    print()

    # Generate in batches
    total_files = 0
    total_bytes = 0
    spans_generated = 0

    batch_num = 0
    while spans_generated < span_count:
        batch_num += 1
        batch_size = min(args.batch_size, span_count - spans_generated)

        print(f"Batch {batch_num}: Generating {batch_size:,} spans...", end=" ", flush=True)
        spans = generate_batch(batch_size, services, start_ns, time_range_ns)

        print("writing to S3...", end=" ", flush=True)
        files, bytes_written = write_to_s3(spans, s3, base_path, f"batch{batch_num:04d}")

        total_files += files
        total_bytes += bytes_written
        spans_generated += batch_size

        print(f"done ({files} files, {bytes_written / (1024**2):.1f} MiB)")

        # Progress update every 10 batches
        if batch_num % 10 == 0:
            pct = spans_generated / span_count * 100
            print(f"  Progress: {spans_generated:,} / {span_count:,} ({pct:.1f}%)")

    # Write marker file
    write_active_dataset_marker(s3, args.bucket, prefix, span_count, args.mode)

    print()
    print("=" * 70)
    print("Generation Complete!")
    print("=" * 70)
    print(f"Total spans:    {spans_generated:,}")
    print(f"Total files:    {total_files}")
    print(f"Total size:     {total_bytes / (1024**3):.2f} GiB")
    print(f"Location:       s3://{base_path}/spans/")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
