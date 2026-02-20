#!/usr/bin/env python3
"""Generate 1.5 TiB validation dataset for OTEL span visualization.

This script generates synthetic OTEL spans in batches and writes them to S3.
The dataset is designed to validate out-of-core processing with a Dask cluster
that has 768 GiB total memory (64 workers × 12 GiB).

Target: 2× cluster memory = ~1.5 TiB (~15-20 billion spans)

Usage:
    # Set AWS credentials
    export AWS_ACCESS_KEY_ID="..."
    export AWS_SECRET_ACCESS_KEY="..."
    export AWS_REGION="us-east-1"

    # Run generation (several hours on high-memory EC2)
    uv run python scripts/generate_validation_dataset.py

    # Resume from specific batch
    uv run python scripts/generate_validation_dataset.py --start-batch 50

    # Quick test mode (10M spans)
    uv run python scripts/generate_validation_dataset.py --quick-test

Example sizing:
    --batches 1 --spans-per-batch 10000000   # ~1 GB quick test
    --batches 10 --spans-per-batch 100000000  # ~100 GB development
    --batches 150 --spans-per-batch 100000000 # ~1.5 TiB validation (default)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Schema (inline to avoid dependency on cybersec package)
# -------------------------------------------------------------------------

_span_kind_dict = pa.dictionary(pa.int8(), pa.string())
_status_code_dict = pa.dictionary(pa.int8(), pa.string())

SPANS_SCHEMA = pa.schema(
    [
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
        # Note: date and hour are NOT in the schema because they're partition columns
        # Including them would cause ArrowTypeError when reading (date32 vs string conflict)
    ],
)

# -------------------------------------------------------------------------
# Service Templates for Realistic Data
# -------------------------------------------------------------------------

SERVICE_TEMPLATES = [
    {"name": "api-gateway", "kind": "SERVER", "operations": ["HTTP request", "route", "rate-limit"]},
    {"name": "auth-service", "kind": "SERVER", "operations": ["validate_token", "refresh_token", "login", "logout"]},
    {"name": "user-service", "kind": "SERVER", "operations": ["get_user", "update_user", "list_users", "delete_user"]},
    {"name": "order-service", "kind": "SERVER", "operations": ["create_order", "get_order", "process_order", "cancel_order"]},
    {"name": "inventory-service", "kind": "SERVER", "operations": ["check_stock", "reserve_item", "release_item"]},
    {"name": "payment-service", "kind": "SERVER", "operations": ["process_payment", "refund", "validate_card"]},
    {"name": "notification-service", "kind": "PRODUCER", "operations": ["send_email", "send_sms", "send_push"]},
    {"name": "shipping-service", "kind": "SERVER", "operations": ["calculate_shipping", "create_shipment", "track"]},
    {"name": "analytics-service", "kind": "CONSUMER", "operations": ["process_event", "aggregate", "report"]},
    {"name": "database", "kind": "CLIENT", "operations": ["SELECT", "INSERT", "UPDATE", "DELETE"]},
    {"name": "cache", "kind": "CLIENT", "operations": ["GET", "SET", "DEL", "MGET", "SCAN"]},
    {"name": "message-queue", "kind": "PRODUCER", "operations": ["publish", "consume", "ack", "nack"]},
]

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
HTTP_ROUTES = [
    "/api/users", "/api/users/{id}", "/api/orders", "/api/orders/{id}",
    "/api/products", "/api/products/{id}", "/api/auth/login", "/api/auth/logout",
    "/api/health", "/api/metrics", "/api/cart", "/api/checkout",
]
HTTP_STATUS_CODES = [200, 200, 200, 200, 201, 204, 400, 401, 403, 404, 500, 502, 503]


def generate_trace_id() -> str:
    """Generate a 32-character hex trace ID."""
    return uuid.uuid4().hex


def generate_span_id() -> str:
    """Generate a 16-character hex span ID."""
    return uuid.uuid4().hex[:16]


def generate_batch(
    batch_id: int,
    span_count: int,
    start_time: datetime,
    duration_hours: int,
    services: list[dict],
) -> list[dict[str, Any]]:
    """Generate a batch of synthetic spans.

    Args:
        batch_id: Batch identifier for logging
        span_count: Number of spans to generate
        start_time: Start of time range
        duration_hours: Duration to spread spans across
        services: Service templates to use

    Returns:
        List of span dictionaries
    """
    spans = []
    time_range_ns = duration_hours * 3600 * 1_000_000_000
    start_ns = int(start_time.timestamp() * 1_000_000_000)

    # Pre-compute service list for faster random selection
    service_count = len(services)

    for i in range(span_count):
        # Random timestamp within range
        offset_ns = random.randint(0, time_range_ns)
        span_start_ns = start_ns + offset_ns

        # Random duration (exponential distribution, avg 50ms)
        duration_ns = int(random.expovariate(1 / 50_000_000))  # 50ms average
        duration_ns = max(1_000_000, min(duration_ns, 30_000_000_000))  # 1ms - 30s

        span_end_ns = span_start_ns + duration_ns

        # Select random service
        service = services[random.randint(0, service_count - 1)]
        operation = random.choice(service["operations"])

        # 5% error rate
        is_error = random.random() < 0.05
        status_code = "ERROR" if is_error else "OK"

        # Generate attributes based on service kind
        attrs: dict[str, Any] = {}
        http_method = None
        http_status = None
        http_url = None
        http_route = None
        db_system = None
        db_name = None
        db_operation = None
        messaging_system = None
        messaging_dest = None
        messaging_op = None

        if service["kind"] == "SERVER":
            http_method = random.choice(HTTP_METHODS)
            http_route = random.choice(HTTP_ROUTES)
            http_status = random.choice(HTTP_STATUS_CODES)
            http_url = f"https://api.example.com{http_route}"
            attrs = {
                "http.method": http_method,
                "http.route": http_route,
                "http.status_code": http_status,
            }
        elif service["kind"] == "CLIENT":
            db_system = random.choice(["postgresql", "mysql", "redis", "mongodb"])
            db_name = "production"
            db_operation = operation
            attrs = {
                "db.system": db_system,
                "db.name": db_name,
                "db.operation": db_operation,
            }
        elif service["kind"] in ("PRODUCER", "CONSUMER"):
            messaging_system = random.choice(["kafka", "rabbitmq", "sqs"])
            messaging_dest = random.choice(["orders", "notifications", "events", "analytics"])
            messaging_op = "publish" if service["kind"] == "PRODUCER" else "consume"
            attrs = {
                "messaging.system": messaging_system,
                "messaging.destination": messaging_dest,
                "messaging.operation": messaging_op,
            }

        # Compute date/hour for partitioning
        dt = datetime.fromtimestamp(span_start_ns / 1_000_000_000, tz=timezone.utc)

        # Generate trace/span IDs (simplified: each span is its own trace for speed)
        # In real data, spans would be grouped into traces
        trace_id = generate_trace_id()
        span_id = generate_span_id()

        span = {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": None,  # Root spans for simplicity
            "start_time_unix_nano": span_start_ns,
            "end_time_unix_nano": span_end_ns,
            "duration_ns": duration_ns,
            "name": operation,
            "kind": service["kind"],
            "status_code": status_code,
            "status_message": "Error occurred" if is_error else None,
            "service_name": service["name"],
            "service_namespace": "production",
            "service_version": "1.0.0",
            "host_name": f"{service['name']}-{random.randint(1, 10)}.example.com",
            "host_ip": f"10.0.{random.randint(1, 255)}.{random.randint(1, 255)}",
            "attributes_json": json.dumps(attrs),
            "resource_attributes_json": json.dumps({"service.name": service["name"]}),
            "http_method": http_method,
            "http_status_code": http_status,
            "http_url": http_url,
            "http_route": http_route,
            "http_target": None,
            "db_system": db_system,
            "db_name": db_name,
            "db_operation": db_operation,
            "db_statement": None,
            "rpc_system": None,
            "rpc_service": None,
            "rpc_method": None,
            "messaging_system": messaging_system,
            "messaging_destination": messaging_dest,
            "messaging_operation": messaging_op,
            "exception_type": "RuntimeError" if is_error else None,
            "exception_message": "Something went wrong" if is_error else None,
            "events_count": random.randint(0, 3),
            "links_count": 0,
            # date and hour are stored as partition paths, not in the parquet data
            # This avoids ArrowTypeError when Dask reads (date32 vs string conflict)
            "_partition_date": dt.date(),
            "_partition_hour": dt.hour,
        }
        spans.append(span)

        # Progress logging
        if (i + 1) % 10_000_000 == 0:
            logger.info(f"  Batch {batch_id}: Generated {(i+1):,} spans")

    return spans


def write_partitioned(
    spans: list[dict[str, Any]],
    filesystem: fs.FileSystem,
    base_path: str,
    batch_id: int,
) -> tuple[int, int]:
    """Write spans to partitioned Parquet files.

    Args:
        spans: List of span dictionaries
        filesystem: PyArrow filesystem
        base_path: Base path for output (without s3:// prefix)
        batch_id: Batch identifier for file naming

    Returns:
        Tuple of (files_written, total_bytes)
    """
    # Group by date/hour partition (using partition keys)
    partitions: dict[tuple, list[dict]] = {}
    for span in spans:
        key = (span["_partition_date"], span["_partition_hour"])
        if key not in partitions:
            partitions[key] = []
        # Remove partition columns from the data (they're in the path, not the file)
        span_data = {k: v for k, v in span.items() if not k.startswith("_partition_")}
        partitions[key].append(span_data)

    files_written = 0
    total_bytes = 0

    for (date_val, hour_val), partition_spans in partitions.items():
        date_str = date_val.isoformat()
        output_dir = f"{base_path}/spans/date={date_str}/hour={hour_val:02d}"

        # Create directory
        try:
            filesystem.create_dir(output_dir)
        except Exception:
            pass  # Directory may already exist

        # Write Parquet file
        file_name = f"batch_{batch_id:04d}_{uuid.uuid4().hex[:8]}.parquet"
        full_path = f"{output_dir}/{file_name}"

        table = pa.Table.from_pylist(partition_spans, schema=SPANS_SCHEMA)

        pq.write_table(
            table,
            full_path,
            filesystem=filesystem,
            row_group_size=100_000,
            compression="snappy",
        )

        # Get file size
        try:
            info = filesystem.get_file_info(full_path)
            total_bytes += info.size
        except Exception:
            pass

        files_written += 1

    return files_written, total_bytes


def get_progress_file() -> Path:
    """Get path to progress tracking file."""
    return Path.home() / ".cache" / "cybersec" / "datagen_progress.json"


def load_progress() -> dict:
    """Load generation progress from disk."""
    progress_file = get_progress_file()
    if progress_file.exists():
        return json.loads(progress_file.read_text())
    return {"completed_batches": [], "total_bytes": 0, "total_spans": 0}


def save_progress(progress: dict) -> None:
    """Save generation progress to disk."""
    progress_file = get_progress_file()
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(progress, indent=2))


def update_active_dataset(bucket: str, prefix: str, total_spans: int, total_bytes: int) -> None:
    """Update the active dataset marker in S3.

    This marker is read by panel-viz to auto-swap to the large dataset
    when generation completes.
    """
    region = os.getenv("AWS_REGION", "us-east-1")
    s3 = fs.S3FileSystem(
        access_key=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region=region,
    )

    marker = {
        "dataset": prefix,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "large",
        "total_spans": total_spans,
        "total_bytes": total_bytes,
    }

    marker_path = f"{bucket}/_active_dataset.json"
    with s3.open_output_stream(marker_path) as f:
        f.write(json.dumps(marker).encode())

    logger.info(f"Updated active dataset marker: s3://{bucket}/_active_dataset.json")
    logger.info("Panel viz will auto-swap to large dataset on next poll")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate validation dataset for OTEL span visualization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--batches",
        type=int,
        default=150,
        help="Number of batches to generate (default: 150)",
    )
    parser.add_argument(
        "--spans-per-batch",
        type=int,
        default=100_000_000,
        help="Spans per batch (default: 100M)",
    )
    parser.add_argument(
        "--start-batch",
        type=int,
        default=0,
        help="Start from this batch number (for resuming)",
    )
    parser.add_argument(
        "--duration-days",
        type=int,
        default=7,
        help="Duration in days to spread data across (default: 7)",
    )
    parser.add_argument(
        "--bucket",
        default="cybersec-data",
        help="S3 bucket name (default: cybersec-data)",
    )
    parser.add_argument(
        "--prefix",
        default="otel",
        help="S3 prefix (default: otel)",
    )
    parser.add_argument(
        "--quick-test",
        action="store_true",
        help="Quick test mode: 1 batch, 10M spans",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last saved progress",
    )

    args = parser.parse_args()

    # Quick test mode overrides
    if args.quick_test:
        args.batches = 1
        args.spans_per_batch = 10_000_000
        logger.info("Quick test mode: 1 batch, 10M spans")

    # Calculate totals
    total_spans = args.batches * args.spans_per_batch
    # Estimate: ~100 bytes per span with snappy compression
    estimated_size_bytes = total_spans * 100
    estimated_size_tib = estimated_size_bytes / (1024**4)

    logger.info("=" * 60)
    logger.info("OTEL Validation Dataset Generator")
    logger.info("=" * 60)
    logger.info(f"Target: s3://{args.bucket}/{args.prefix}/")
    logger.info(f"Batches: {args.batches}")
    logger.info(f"Spans per batch: {args.spans_per_batch:,}")
    logger.info(f"Total spans: {total_spans:,}")
    logger.info(f"Estimated size: ~{estimated_size_tib:.2f} TiB")
    logger.info(f"Duration: {args.duration_days} days")
    logger.info(f"Services: {len(SERVICE_TEMPLATES)}")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN - no data will be written")
        return

    # Check AWS credentials
    if not os.getenv("AWS_ACCESS_KEY_ID"):
        logger.error("AWS_ACCESS_KEY_ID not set")
        sys.exit(1)
    if not os.getenv("AWS_SECRET_ACCESS_KEY"):
        logger.error("AWS_SECRET_ACCESS_KEY not set")
        sys.exit(1)

    # Initialize S3 filesystem
    region = os.getenv("AWS_REGION", "us-east-1")
    s3 = fs.S3FileSystem(
        access_key=os.getenv("AWS_ACCESS_KEY_ID"),
        secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region=region,
    )
    base_path = f"{args.bucket}/{args.prefix}"

    # Load progress if resuming
    if args.resume:
        progress = load_progress()
        completed = set(progress.get("completed_batches", []))
        logger.info(f"Resuming: {len(completed)} batches already completed")
    else:
        progress = {"completed_batches": [], "total_bytes": 0, "total_spans": 0}
        completed = set()

    # Calculate time range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.duration_days)

    # Generate batches
    overall_start = time.time()

    for batch_id in range(args.start_batch, args.batches):
        if batch_id in completed:
            logger.info(f"Batch {batch_id + 1}/{args.batches}: Already complete, skipping")
            continue

        batch_start = time.time()
        logger.info(f"Batch {batch_id + 1}/{args.batches}: Generating {args.spans_per_batch:,} spans...")

        # Generate spans
        spans = generate_batch(
            batch_id=batch_id,
            span_count=args.spans_per_batch,
            start_time=start_time,
            duration_hours=args.duration_days * 24,
            services=SERVICE_TEMPLATES,
        )

        # Write to S3
        logger.info(f"Batch {batch_id + 1}: Writing to S3...")
        files_written, bytes_written = write_partitioned(
            spans=spans,
            filesystem=s3,
            base_path=base_path,
            batch_id=batch_id,
        )

        batch_elapsed = time.time() - batch_start

        # Update progress
        progress["completed_batches"].append(batch_id)
        progress["total_bytes"] += bytes_written
        progress["total_spans"] += len(spans)
        save_progress(progress)

        # Log progress
        total_gib = progress["total_bytes"] / (1024**3)
        logger.info(
            f"Batch {batch_id + 1}: Complete in {batch_elapsed:.1f}s - "
            f"{files_written} files, {bytes_written / (1024**3):.2f} GiB"
        )
        logger.info(
            f"Progress: {len(progress['completed_batches'])}/{args.batches} batches, "
            f"{progress['total_spans']:,} spans, {total_gib:.2f} GiB"
        )

        # Clear memory
        del spans

    overall_elapsed = time.time() - overall_start
    hours = overall_elapsed / 3600

    logger.info("=" * 60)
    logger.info("Generation Complete!")
    logger.info("=" * 60)
    logger.info(f"Total time: {hours:.2f} hours")
    logger.info(f"Total spans: {progress['total_spans']:,}")
    logger.info(f"Total size: {progress['total_bytes'] / (1024**4):.3f} TiB")
    logger.info(f"Location: s3://{args.bucket}/{args.prefix}/spans/")
    logger.info("=" * 60)

    # Update active dataset marker for panel-viz auto-swap
    if len(progress.get("completed_batches", [])) == args.batches:
        update_active_dataset(
            bucket=args.bucket,
            prefix=args.prefix,
            total_spans=progress["total_spans"],
            total_bytes=progress["total_bytes"],
        )


if __name__ == "__main__":
    main()
