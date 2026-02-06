"""Tests for benchmark metrics."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from cybersec.benchmarks.metrics import (
    BenchmarkMetrics,
    BenchmarkRun,
    ScalingResult,
    EnvironmentMetadata,
)


def test_benchmark_metrics_basic():
    """Test basic BenchmarkMetrics creation."""
    metrics = BenchmarkMetrics(
        run_id=1,
        worker_count=4,
        zoom_level="full",
        zoom_fraction=1.0,
        wall_clock_ns=1_000_000_000,  # 1 second
        points_processed=1_000_000,
    )

    assert metrics.run_id == 1
    assert metrics.worker_count == 4
    assert metrics.wall_clock_seconds == 1.0


def test_benchmark_metrics_derived():
    """Test derived metric calculations."""
    metrics = BenchmarkMetrics(
        run_id=1,
        worker_count=4,
        zoom_level="full",
        zoom_fraction=1.0,
        wall_clock_ns=2_000_000_000,  # 2 seconds
        aggregation_ns=1_500_000_000,  # 1.5 seconds
        shade_ns=500_000_000,  # 0.5 seconds
        memory_per_worker=[100, 200, 300, 400],
    )

    assert metrics.wall_clock_seconds == 2.0
    assert metrics.aggregation_seconds == 1.5
    assert metrics.shade_seconds == 0.5
    assert metrics.total_memory_bytes == 1000
    assert metrics.mean_memory_bytes == 250


def test_benchmark_metrics_serialization():
    """Test metrics serialization round-trip."""
    metrics = BenchmarkMetrics(
        run_id=42,
        worker_count=8,
        zoom_level="10pct",
        zoom_fraction=0.1,
        wall_clock_ns=5_000_000_000,
        points_processed=10_000_000,
        points_per_second=2_000_000,
    )

    data = metrics.to_dict()
    loaded = BenchmarkMetrics.from_dict(data)

    assert loaded.run_id == metrics.run_id
    assert loaded.worker_count == metrics.worker_count
    assert loaded.zoom_level == metrics.zoom_level
    assert loaded.wall_clock_ns == metrics.wall_clock_ns


def test_benchmark_run_basic():
    """Test basic BenchmarkRun creation."""
    run = BenchmarkRun(
        config_hash="abc123",
        run_name="test-run",
    )

    assert run.config_hash == "abc123"
    assert run.run_name == "test-run"
    assert run.completed_at is None
    assert len(run.metrics) == 0


def test_benchmark_run_add_metrics():
    """Test adding metrics to a run."""
    run = BenchmarkRun(config_hash="abc", run_name="test")

    # Add warmup
    run.add_metric(BenchmarkMetrics(
        run_id=0, worker_count=2, zoom_level="full",
        zoom_fraction=1.0, is_warmup=True
    ))

    # Add production
    run.add_metric(BenchmarkMetrics(
        run_id=1, worker_count=2, zoom_level="full",
        zoom_fraction=1.0, is_warmup=False
    ))

    assert len(run.metrics) == 2
    assert len(run.warmup_metrics) == 1
    assert len(run.production_metrics) == 1


def test_benchmark_run_mark_complete():
    """Test marking run as complete."""
    run = BenchmarkRun(config_hash="abc", run_name="test")

    assert run.duration_seconds is None

    run.mark_complete()

    assert run.completed_at is not None
    assert run.duration_seconds is not None
    assert run.duration_seconds >= 0


def test_benchmark_run_serialization(tmp_path):
    """Test run serialization round-trip."""
    run = BenchmarkRun(config_hash="abc123", run_name="test-run")

    run.add_metric(BenchmarkMetrics(
        run_id=0, worker_count=4, zoom_level="full", zoom_fraction=1.0
    ))
    run.mark_complete()

    # Save
    run_path = tmp_path / "run.json"
    run.save(run_path)

    # Load
    loaded = BenchmarkRun.load(run_path)

    assert loaded.config_hash == run.config_hash
    assert loaded.run_name == run.run_name
    assert len(loaded.metrics) == 1
    assert loaded.completed_at is not None


def test_scaling_result():
    """Test ScalingResult."""
    result = ScalingResult(
        worker_count=8,
        zoom_level="full",
        mean_time_s=10.5,
        std_time_s=0.5,
        speedup=6.2,
        efficiency=0.775,
        throughput_pts_s=5_000_000,
        throughput_gb_s=2.5,
    )

    assert result.worker_count == 8
    assert result.efficiency == 0.775

    data = result.to_dict()
    assert data["speedup"] == 6.2


def test_environment_metadata_gather():
    """Test environment metadata gathering."""
    meta = EnvironmentMetadata.gather()

    assert meta.python_version != ""
    assert meta.cpu_cores > 0 or meta.cpu_cores == 0  # May be 0 on some systems


def test_environment_metadata_serialization():
    """Test environment metadata serialization."""
    meta = EnvironmentMetadata.gather()
    data = meta.to_dict()

    assert "python_version" in data
    assert "cpu_cores" in data
    assert "dask_version" in data
