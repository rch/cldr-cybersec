"""Tests for seeded dataset generation."""

import pytest
import numpy as np
import pandas as pd

from cybersec.benchmarks.config import BenchmarkConfig
from cybersec.benchmarks.dataset import (
    generate_spans_seeded,
    compute_dataset_hash,
    verify_partition_reproducibility,
    estimate_partition_size,
    DatasetGenerator,
)


@pytest.fixture
def small_config():
    """Small config for testing."""
    return BenchmarkConfig(
        total_spans=1000,
        partitions=10,
        random_seed=42,
    )


def test_generate_spans_basic(small_config):
    """Test basic span generation."""
    df = generate_spans_seeded(0, small_config)

    assert len(df) == small_config.spans_per_partition
    assert "trace_id" in df.columns
    assert "span_id" in df.columns
    assert "timestamp_unix" in df.columns
    assert "duration_ms" in df.columns


def test_generate_spans_reproducible(small_config):
    """Test that generation is reproducible with same seed."""
    df1 = generate_spans_seeded(0, small_config)
    df2 = generate_spans_seeded(0, small_config)

    # DataFrames should be identical
    pd.testing.assert_frame_equal(df1, df2)


def test_generate_spans_different_partitions(small_config):
    """Test that different partitions have different data."""
    df0 = generate_spans_seeded(0, small_config)
    df1 = generate_spans_seeded(1, small_config)

    # Different partition IDs
    assert df0["partition_id"].iloc[0] == 0
    assert df1["partition_id"].iloc[0] == 1

    # Different trace IDs (statistically certain)
    assert df0["trace_id"].iloc[0] != df1["trace_id"].iloc[0]


def test_generate_spans_timestamps_in_partition_window(small_config):
    """Test that timestamps fall within partition's time window."""
    partition_id = 5
    df = generate_spans_seeded(partition_id, small_config)

    # Start times should be within the partition's hour
    start_times = pd.to_datetime(df["start_time"])
    min_time = start_times.min()
    max_time = start_times.max()

    # All times should be within a 1-hour window
    time_range = (max_time - min_time).total_seconds()
    assert time_range <= 3600  # 1 hour


def test_generate_spans_duration_range(small_config):
    """Test that durations are within expected range."""
    df = generate_spans_seeded(0, small_config)

    durations = df["duration_ms"]
    assert durations.min() >= 1  # Min 1ms
    assert durations.max() <= 10000  # Max 10s


def test_generate_spans_service_distribution(small_config):
    """Test that service names have expected distribution."""
    df = generate_spans_seeded(0, small_config)

    services = df["service_name"].value_counts()
    # api-gateway should be most common (~25%)
    top_service = services.index[0]
    assert top_service == "api-gateway"


def test_compute_dataset_hash():
    """Test dataset hash computation."""
    config1 = BenchmarkConfig(total_spans=1000, random_seed=42)
    config2 = BenchmarkConfig(total_spans=1000, random_seed=42)
    config3 = BenchmarkConfig(total_spans=1000, random_seed=43)

    hash1 = compute_dataset_hash(config1)
    hash2 = compute_dataset_hash(config2)
    hash3 = compute_dataset_hash(config3)

    # Same config = same hash
    assert hash1 == hash2
    # Different seed = different hash
    assert hash1 != hash3


def test_verify_partition_reproducibility(small_config):
    """Test reproducibility verification."""
    is_reproducible, hash_val = verify_partition_reproducibility(0, small_config)

    assert is_reproducible is True
    assert len(hash_val) == 16  # 16 hex chars


def test_verify_partition_with_expected_hash(small_config):
    """Test verification with expected hash."""
    # First get the hash
    _, expected_hash = verify_partition_reproducibility(0, small_config)

    # Verify with expected hash
    is_reproducible, _ = verify_partition_reproducibility(
        0, small_config, expected_hash=expected_hash
    )
    assert is_reproducible is True

    # Verify with wrong hash
    is_reproducible, _ = verify_partition_reproducibility(
        0, small_config, expected_hash="wrong_hash"
    )
    assert is_reproducible is False


def test_estimate_partition_size(small_config):
    """Test partition size estimation."""
    sizes = estimate_partition_size(small_config)

    assert "memory_mb_per_partition" in sizes
    assert "disk_mb_per_partition" in sizes
    assert "total_memory_gb" in sizes
    assert "total_disk_gb" in sizes

    # Disk should be smaller than memory due to compression
    assert sizes["disk_mb_per_partition"] < sizes["memory_mb_per_partition"]


def test_dataset_generator_basic(small_config):
    """Test DatasetGenerator."""
    gen = DatasetGenerator(small_config)

    assert gen.dataset_hash == compute_dataset_hash(small_config)

    df = gen.generate_partition(0)
    assert len(df) == small_config.spans_per_partition


def test_dataset_generator_paths(small_config):
    """Test DatasetGenerator path generation."""
    gen = DatasetGenerator(small_config)

    part_path = gen.partition_path(42)
    assert "part-00042.parquet" in part_path

    meta_path = gen.metadata_path()
    assert "_metadata.json" in meta_path


def test_dataset_generator_metadata(small_config):
    """Test metadata generation."""
    gen = DatasetGenerator(small_config)
    meta = gen.create_metadata()

    assert meta["version"] == small_config.version
    assert meta["total_spans"] == small_config.total_spans
    assert meta["partitions"] == small_config.partitions
    assert "schema" in meta
    assert "created_at" in meta


def test_trace_id_format(small_config):
    """Test that trace IDs have correct format."""
    df = generate_spans_seeded(0, small_config)

    for trace_id in df["trace_id"].head(10):
        assert len(trace_id) == 32  # 128-bit hex
        assert all(c in "0123456789abcdef" for c in trace_id)


def test_span_id_format(small_config):
    """Test that span IDs have correct format."""
    df = generate_spans_seeded(0, small_config)

    for span_id in df["span_id"].head(10):
        assert len(span_id) == 16  # 64-bit hex
        assert all(c in "0123456789abcdef" for c in span_id)
