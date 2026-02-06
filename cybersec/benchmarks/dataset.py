"""Seeded dataset generation for Dask/Datashader benchmarks.

This module generates OTEL span data with per-partition seeding
for exact reproducibility across runs and environments.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import BenchmarkConfig


def generate_spans_seeded(
    partition_id: int,
    config: BenchmarkConfig,
    base_timestamp: Optional[datetime] = None,
) -> pd.DataFrame:
    """Generate spans for a single partition with deterministic seeding.

    The random seed is computed as config.random_seed + partition_id,
    ensuring each partition generates identical data across runs.

    Args:
        partition_id: Partition index (0 to config.partitions - 1)
        config: Benchmark configuration
        base_timestamp: Base timestamp for span times (default: 2024-01-01)

    Returns:
        DataFrame with span data matching OTEL schema
    """
    # Per-partition seeding for exact reproducibility
    seed = config.random_seed + partition_id
    rng = np.random.Generator(np.random.PCG64(seed))

    n_spans = config.spans_per_partition

    if base_timestamp is None:
        base_timestamp = datetime(2024, 1, 1)

    # Generate trace and span IDs (hex strings)
    # Use bytes and convert to hex for performance (avoids int64 overflow)
    trace_ids = [rng.bytes(16).hex() for _ in range(n_spans)]
    span_ids = [rng.bytes(8).hex() for _ in range(n_spans)]

    # Parent span IDs (80% have parents, 20% are root spans)
    parent_probs = rng.random(n_spans)
    parent_span_ids = [
        rng.bytes(8).hex() if p > 0.2 else ""
        for p in parent_probs
    ]

    # Service names (weighted distribution)
    services = [
        "api-gateway", "user-service", "order-service", "payment-service",
        "inventory-service", "notification-service", "auth-service",
        "cache-service", "db-proxy", "search-service"
    ]
    service_weights = [0.25, 0.15, 0.15, 0.1, 0.1, 0.08, 0.07, 0.05, 0.03, 0.02]
    service_names = rng.choice(services, size=n_spans, p=service_weights)

    # Span names (HTTP methods and paths)
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]
    method_weights = [0.5, 0.25, 0.1, 0.1, 0.05]
    paths = [
        "/api/v1/users", "/api/v1/orders", "/api/v1/products",
        "/api/v1/auth/login", "/api/v1/search", "/health", "/metrics"
    ]
    span_names = [
        f"{rng.choice(methods, p=method_weights)} {rng.choice(paths)}"
        for _ in range(n_spans)
    ]

    # Timestamps: spread across partition's time window
    # Each partition covers 1 hour of time
    partition_start = base_timestamp + timedelta(hours=partition_id)
    start_offsets_ms = rng.integers(0, 3600000, size=n_spans)  # 0-60 minutes
    start_times = [
        partition_start + timedelta(milliseconds=int(offset))
        for offset in start_offsets_ms
    ]

    # Durations: log-normal distribution (mean ~50ms, long tail to 10s)
    durations_ms = rng.lognormal(mean=3.9, sigma=1.5, size=n_spans)
    durations_ms = np.clip(durations_ms, 1, 10000)  # 1ms to 10s

    end_times = [
        start + timedelta(milliseconds=float(dur))
        for start, dur in zip(start_times, durations_ms)
    ]

    # Status codes (mostly success, some errors)
    status_probs = rng.random(n_spans)
    status_codes = np.where(status_probs > 0.05, 0, 2)  # 0=OK, 2=ERROR

    # HTTP status codes (for error spans)
    http_status_codes = np.where(
        status_codes == 0,
        rng.choice([200, 201, 204], size=n_spans, p=[0.7, 0.2, 0.1]),
        rng.choice([400, 401, 403, 404, 500, 502, 503], size=n_spans,
                   p=[0.15, 0.1, 0.1, 0.2, 0.2, 0.15, 0.1])
    )

    # Numeric columns for datashader visualization
    # X-axis: timestamp as Unix epoch (seconds)
    timestamp_unix = np.array([t.timestamp() for t in start_times])

    # Y-axis: duration in milliseconds
    duration_ms_array = durations_ms.astype(np.float64)

    # Additional numeric attributes
    request_sizes = rng.lognormal(mean=6, sigma=2, size=n_spans).astype(np.int64)
    request_sizes = np.clip(request_sizes, 100, 10_000_000)  # 100B to 10MB

    response_sizes = rng.lognormal(mean=7, sigma=2, size=n_spans).astype(np.int64)
    response_sizes = np.clip(response_sizes, 100, 100_000_000)  # 100B to 100MB

    # Create DataFrame
    df = pd.DataFrame({
        # OTEL standard fields
        "trace_id": trace_ids,
        "span_id": span_ids,
        "parent_span_id": parent_span_ids,
        "service_name": service_names,
        "span_name": span_names,
        "start_time": start_times,
        "end_time": end_times,
        "duration_ms": duration_ms_array,
        "status_code": status_codes,
        "http_status_code": http_status_codes,

        # Numeric columns for visualization
        "timestamp_unix": timestamp_unix,
        "request_size_bytes": request_sizes,
        "response_size_bytes": response_sizes,

        # Metadata
        "partition_id": partition_id,
    })

    return df


def compute_dataset_hash(config: BenchmarkConfig) -> str:
    """Compute deterministic hash of dataset configuration.

    This hash uniquely identifies a dataset based on its generation
    parameters. Same hash = identical data.

    Args:
        config: Benchmark configuration

    Returns:
        SHA256 hash of configuration (first 16 chars)
    """
    hash_input = (
        f"v={config.version};"
        f"spans={config.total_spans};"
        f"parts={config.partitions};"
        f"seed={config.random_seed}"
    )
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


def verify_partition_reproducibility(
    partition_id: int,
    config: BenchmarkConfig,
    expected_hash: Optional[str] = None,
) -> tuple[bool, str]:
    """Verify that partition generation is reproducible.

    Generates the partition twice and compares hashes.

    Args:
        partition_id: Partition to verify
        config: Benchmark configuration
        expected_hash: Optional expected hash to compare against

    Returns:
        Tuple of (is_reproducible, computed_hash)
    """
    df1 = generate_spans_seeded(partition_id, config)
    df2 = generate_spans_seeded(partition_id, config)

    # Compare DataFrame hashes
    hash1 = hashlib.sha256(
        pd.util.hash_pandas_object(df1).values.tobytes()
    ).hexdigest()[:16]

    hash2 = hashlib.sha256(
        pd.util.hash_pandas_object(df2).values.tobytes()
    ).hexdigest()[:16]

    is_reproducible = hash1 == hash2

    if expected_hash is not None:
        is_reproducible = is_reproducible and (hash1 == expected_hash)

    return is_reproducible, hash1


def estimate_partition_size(config: BenchmarkConfig) -> dict[str, float]:
    """Estimate partition size in memory and on disk.

    Args:
        config: Benchmark configuration

    Returns:
        Dictionary with size estimates in MB
    """
    # Generate a small sample to estimate size
    sample_config = BenchmarkConfig(
        total_spans=1000,
        partitions=1,
        random_seed=config.random_seed,
    )
    df = generate_spans_seeded(0, sample_config)

    # Memory size
    memory_mb_per_row = df.memory_usage(deep=True).sum() / len(df) / (1024 * 1024)

    # Disk size estimate (Parquet with Snappy ~5:1 compression)
    disk_mb_per_row = memory_mb_per_row / 5

    spans_per_partition = config.spans_per_partition

    return {
        "memory_mb_per_partition": memory_mb_per_row * spans_per_partition,
        "disk_mb_per_partition": disk_mb_per_row * spans_per_partition,
        "total_memory_gb": memory_mb_per_row * config.total_spans / 1024,
        "total_disk_gb": disk_mb_per_row * config.total_spans / 1024,
    }


class DatasetGenerator:
    """Generator for scaling benchmark datasets.

    Manages dataset creation, upload to S3, and verification.
    """

    def __init__(self, config: BenchmarkConfig):
        """Initialize generator with configuration.

        Args:
            config: Benchmark configuration
        """
        self.config = config
        self.dataset_hash = compute_dataset_hash(config)

    def generate_partition(self, partition_id: int) -> pd.DataFrame:
        """Generate a single partition.

        Args:
            partition_id: Partition index

        Returns:
            DataFrame with partition data
        """
        return generate_spans_seeded(partition_id, self.config)

    def partition_path(self, partition_id: int) -> str:
        """Get S3 path for a partition.

        Args:
            partition_id: Partition index

        Returns:
            S3 path like s3://bucket/prefix/part-00000.parquet
        """
        return (
            f"s3://{self.config.s3_bucket}/{self.config.s3_prefix}/"
            f"part-{partition_id:05d}.parquet"
        )

    def metadata_path(self) -> str:
        """Get S3 path for dataset metadata.

        Returns:
            S3 path to _metadata.json
        """
        return (
            f"s3://{self.config.s3_bucket}/{self.config.s3_prefix}/"
            f"_metadata.json"
        )

    def create_metadata(self) -> dict:
        """Create dataset metadata dictionary.

        Returns:
            Metadata dictionary
        """
        return {
            "version": self.config.version,
            "dataset_hash": self.dataset_hash,
            "total_spans": self.config.total_spans,
            "partitions": self.config.partitions,
            "spans_per_partition": self.config.spans_per_partition,
            "random_seed": self.config.random_seed,
            "created_at": datetime.now().isoformat(),
            "schema": {
                "trace_id": "string",
                "span_id": "string",
                "parent_span_id": "string",
                "service_name": "string",
                "span_name": "string",
                "start_time": "datetime64[ns]",
                "end_time": "datetime64[ns]",
                "duration_ms": "float64",
                "status_code": "int64",
                "http_status_code": "int64",
                "timestamp_unix": "float64",
                "request_size_bytes": "int64",
                "response_size_bytes": "int64",
                "partition_id": "int64",
            },
        }
