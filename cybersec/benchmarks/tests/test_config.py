"""Tests for benchmark configuration."""

import pytest
from pathlib import Path
import tempfile

from cybersec.benchmarks.config import (
    BenchmarkConfig,
    BENCHMARK_FULL,
    BENCHMARK_QUICK,
)


def test_default_config():
    """Test default configuration values."""
    config = BenchmarkConfig()

    assert config.total_spans == 240_000_000
    assert config.partitions == 2400
    assert config.random_seed == 42
    assert config.spans_per_partition == 100_000


def test_spans_per_partition():
    """Test spans per partition calculation."""
    config = BenchmarkConfig(total_spans=1000, partitions=10)
    assert config.spans_per_partition == 100


def test_total_runs():
    """Test total runs calculation."""
    config = BenchmarkConfig(
        worker_counts=[2, 4],
        zoom_levels=[("full", 1.0), ("10pct", 0.1)],
        runs_per_config=3,
        warmup_runs=1,
    )
    # 2 workers * 2 zooms * (3 + 1) runs = 16
    assert config.total_runs == 16


def test_validation_valid():
    """Test validation passes for valid config."""
    config = BenchmarkConfig()
    errors = config.validate()
    assert errors == []


def test_validation_invalid_spans():
    """Test validation catches invalid spans."""
    config = BenchmarkConfig(total_spans=0)
    errors = config.validate()
    assert "total_spans must be positive" in errors


def test_validation_non_divisible():
    """Test validation catches non-divisible spans/partitions."""
    config = BenchmarkConfig(total_spans=100, partitions=7)
    errors = config.validate()
    assert any("divisible" in e for e in errors)


def test_validation_empty_workers():
    """Test validation catches empty worker list."""
    config = BenchmarkConfig(worker_counts=[])
    errors = config.validate()
    assert "worker_counts must not be empty" in errors


def test_validation_invalid_zoom():
    """Test validation catches invalid zoom fraction."""
    config = BenchmarkConfig(zoom_levels=[("bad", 1.5)])
    errors = config.validate()
    assert any("zoom level" in e for e in errors)


def test_s3_path():
    """Test S3 path construction."""
    config = BenchmarkConfig(
        s3_bucket="my-bucket",
        s3_prefix="data/test",
    )
    assert config.s3_path == "s3://my-bucket/data/test/"


def test_save_load(tmp_path):
    """Test config save and load."""
    config = BenchmarkConfig(
        total_spans=1000,
        partitions=10,
        worker_counts=[2, 4],
    )

    config_path = tmp_path / "config.toml"
    config.save(config_path)

    loaded = BenchmarkConfig.load(config_path)

    assert loaded.total_spans == config.total_spans
    assert loaded.partitions == config.partitions
    assert loaded.worker_counts == config.worker_counts
    assert loaded.random_seed == config.random_seed


def test_profiles_valid():
    """Test pre-configured profiles are valid."""
    for profile in [BENCHMARK_FULL, BENCHMARK_QUICK]:
        errors = profile.validate()
        assert errors == [], f"Profile {profile} has errors: {errors}"


def test_estimated_size():
    """Test estimated size calculation."""
    config = BenchmarkConfig()
    # ~500 bytes per span * 240M spans = ~120GB
    assert 100 < config.estimated_size_gb < 150
    assert config.estimated_disk_size_gb < config.estimated_size_gb
