"""Tests for benchmark analysis."""

import pytest
import numpy as np

from cybersec.benchmarks.metrics import BenchmarkMetrics, BenchmarkRun
from cybersec.benchmarks.analysis import (
    compute_summary_statistics,
    group_metrics_by_config,
    compute_scaling_results,
    estimate_amdahls_fraction,
    compute_strong_scaling_efficiency,
    analyze_run,
)


def create_test_metrics(worker_count, zoom_level, time_s, runs=3):
    """Create test metrics for a configuration."""
    metrics = []
    for i in range(runs):
        # Add some variation
        time_ns = int((time_s + np.random.normal(0, time_s * 0.1)) * 1e9)
        metrics.append(BenchmarkMetrics(
            run_id=i,
            worker_count=worker_count,
            zoom_level=zoom_level,
            zoom_fraction=1.0 if zoom_level == "full" else 0.1,
            wall_clock_ns=time_ns,
            points_processed=1_000_000,
            points_per_second=1_000_000 / (time_ns / 1e9),
            gb_processed=0.5,
            gb_per_second=0.5 / (time_ns / 1e9),
        ))
    return metrics


@pytest.fixture
def sample_metrics():
    """Create sample metrics for testing."""
    metrics = []
    # Simulate scaling: 2 workers = 10s, 4 workers = 6s, 8 workers = 4s
    metrics.extend(create_test_metrics(2, "full", 10.0))
    metrics.extend(create_test_metrics(4, "full", 6.0))
    metrics.extend(create_test_metrics(8, "full", 4.0))
    return metrics


@pytest.fixture
def sample_run(sample_metrics):
    """Create sample benchmark run."""
    run = BenchmarkRun(config_hash="test123", run_name="test-run")
    for m in sample_metrics:
        run.add_metric(m)
    run.mark_complete()
    return run


def test_compute_summary_statistics():
    """Test summary statistics computation."""
    metrics = create_test_metrics(4, "full", 5.0, runs=10)
    stats = compute_summary_statistics(metrics)

    assert stats["count"] == 10
    assert "mean" in stats["time_s"]
    assert "std" in stats["time_s"]
    assert 4.5 < stats["time_s"]["mean"] < 5.5  # Within 10%


def test_compute_summary_statistics_empty():
    """Test summary statistics with empty list."""
    stats = compute_summary_statistics([])
    assert stats == {}


def test_group_metrics_by_config(sample_metrics):
    """Test grouping metrics by config."""
    groups = group_metrics_by_config(sample_metrics)

    assert (2, "full") in groups
    assert (4, "full") in groups
    assert (8, "full") in groups
    assert len(groups[(2, "full")]) == 3


def test_compute_scaling_results(sample_run):
    """Test scaling results computation."""
    results = compute_scaling_results(sample_run)

    assert len(results) == 3  # 3 worker counts

    # Find results by worker count
    by_workers = {r.worker_count: r for r in results}

    # Baseline should have speedup ~1
    assert 0.9 < by_workers[2].speedup < 1.1

    # 4 workers should have speedup > 1
    assert by_workers[4].speedup > 1.0

    # 8 workers should have highest speedup
    assert by_workers[8].speedup > by_workers[4].speedup


def test_compute_scaling_results_custom_baseline(sample_run):
    """Test scaling results with custom baseline."""
    results = compute_scaling_results(sample_run, baseline_workers=4)

    by_workers = {r.worker_count: r for r in results}

    # 4 workers is now baseline
    assert 0.9 < by_workers[4].speedup < 1.1


def test_estimate_amdahls_fraction():
    """Test Amdahl's fraction estimation."""
    # Create results with known scaling behavior
    # Perfect scaling: speedup = n, efficiency = 1
    results = [
        BenchmarkMetrics(
            run_id=i,
            worker_count=n,
            zoom_level="full",
            zoom_fraction=1.0,
            wall_clock_ns=int(1e9),
        )
        for i, n in enumerate([1, 2, 4, 8])
    ]

    # Actually need ScalingResult objects
    from cybersec.benchmarks.metrics import ScalingResult

    # Simulate ~10% serial fraction
    # Amdahl's: S = 1 / (f + (1-f)/n)
    # f=0.1: n=2 -> S=1.82, n=4 -> S=3.08, n=8 -> S=4.71
    scaling_results = [
        ScalingResult(1, "full", 10.0, 0.5, 1.0, 1.0, 1e6, 0.5),
        ScalingResult(2, "full", 5.5, 0.3, 1.82, 0.91, 1.8e6, 0.9),
        ScalingResult(4, "full", 3.25, 0.2, 3.08, 0.77, 3e6, 1.5),
        ScalingResult(8, "full", 2.12, 0.15, 4.71, 0.59, 4.5e6, 2.2),
    ]

    f = estimate_amdahls_fraction(scaling_results, "full")

    # Should estimate close to 0.1
    assert 0.05 < f < 0.2


def test_compute_strong_scaling_efficiency(sample_run):
    """Test strong scaling efficiency computation."""
    results = compute_scaling_results(sample_run)
    efficiency = compute_strong_scaling_efficiency(results, "full")

    assert efficiency["zoom_level"] == "full"
    assert "max_speedup" in efficiency
    assert "mean_efficiency" in efficiency
    assert efficiency["max_speedup"] > 1.0


def test_analyze_run(sample_run):
    """Test full run analysis."""
    analysis = analyze_run(sample_run)

    assert analysis.run_name == "test-run"
    assert analysis.production_metrics > 0
    assert len(analysis.scaling_results) > 0
    assert len(analysis.recommendations) > 0


def test_analyze_run_generates_recommendations(sample_run):
    """Test that analysis generates appropriate recommendations."""
    # Create run with low efficiency
    run = BenchmarkRun(config_hash="test", run_name="test")

    # Very poor scaling: 32 workers barely faster than 2
    for w, t in [(2, 10.0), (4, 9.5), (8, 9.0), (16, 8.8), (32, 8.5)]:
        for m in create_test_metrics(w, "full", t, runs=3):
            run.add_metric(m)

    run.mark_complete()
    analysis = analyze_run(run)

    # Should have recommendation about low efficiency
    assert any("efficiency" in r.lower() for r in analysis.recommendations)
