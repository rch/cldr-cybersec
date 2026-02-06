"""Statistical analysis for benchmark results.

This module provides functions for computing scaling metrics,
statistical summaries, and derived quantities like Amdahl's fraction.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .metrics import BenchmarkMetrics, BenchmarkRun, ScalingResult


def compute_summary_statistics(
    metrics: list[BenchmarkMetrics],
) -> dict:
    """Compute summary statistics for a list of metrics.

    Args:
        metrics: List of benchmark metrics (should all be same config)

    Returns:
        Dictionary with mean, std, min, max, median for key values
    """
    if not metrics:
        return {}

    times = [m.wall_clock_seconds for m in metrics]
    throughputs_pts = [m.points_per_second for m in metrics]
    throughputs_gb = [m.gb_per_second for m in metrics]
    memories = [m.peak_memory_pct for m in metrics]

    return {
        "count": len(metrics),
        "time_s": {
            "mean": float(np.mean(times)),
            "std": float(np.std(times)),
            "min": float(np.min(times)),
            "max": float(np.max(times)),
            "median": float(np.median(times)),
        },
        "throughput_pts_s": {
            "mean": float(np.mean(throughputs_pts)),
            "std": float(np.std(throughputs_pts)),
        },
        "throughput_gb_s": {
            "mean": float(np.mean(throughputs_gb)),
            "std": float(np.std(throughputs_gb)),
        },
        "memory_pct": {
            "mean": float(np.mean(memories)),
            "max": float(np.max(memories)),
        },
    }


def group_metrics_by_config(
    metrics: list[BenchmarkMetrics],
) -> dict[tuple[int, str], list[BenchmarkMetrics]]:
    """Group metrics by (worker_count, zoom_level).

    Args:
        metrics: List of benchmark metrics

    Returns:
        Dictionary mapping (workers, zoom) to list of metrics
    """
    groups = {}
    for m in metrics:
        key = (m.worker_count, m.zoom_level)
        if key not in groups:
            groups[key] = []
        groups[key].append(m)
    return groups


def compute_scaling_results(
    run: BenchmarkRun,
    baseline_workers: Optional[int] = None,
) -> list[ScalingResult]:
    """Compute scaling results from benchmark run.

    Args:
        run: Benchmark run with metrics
        baseline_workers: Worker count to use as baseline for speedup
                         (default: minimum worker count)

    Returns:
        List of ScalingResult for each configuration
    """
    metrics = run.production_metrics
    if not metrics:
        return []

    # Group by config
    groups = group_metrics_by_config(metrics)

    # Find baseline worker count
    all_workers = sorted(set(m.worker_count for m in metrics))
    if not all_workers:
        return []

    if baseline_workers is None:
        baseline_workers = all_workers[0]

    # Compute baseline times per zoom level
    zoom_levels = sorted(set(m.zoom_level for m in metrics))
    baseline_times = {}

    for zoom in zoom_levels:
        baseline_key = (baseline_workers, zoom)
        if baseline_key in groups:
            baseline_metrics = groups[baseline_key]
            baseline_times[zoom] = np.mean(
                [m.wall_clock_seconds for m in baseline_metrics]
            )
        else:
            baseline_times[zoom] = None

    # Compute scaling results
    results = []
    for (workers, zoom), group_metrics in groups.items():
        stats = compute_summary_statistics(group_metrics)

        mean_time = stats["time_s"]["mean"]
        std_time = stats["time_s"]["std"]

        # Speedup and efficiency
        baseline = baseline_times.get(zoom)
        if baseline and mean_time > 0:
            speedup = baseline / mean_time
            efficiency = speedup / workers
        else:
            speedup = 1.0
            efficiency = 1.0 / workers

        results.append(ScalingResult(
            worker_count=workers,
            zoom_level=zoom,
            mean_time_s=mean_time,
            std_time_s=std_time,
            speedup=speedup,
            efficiency=efficiency,
            throughput_pts_s=stats["throughput_pts_s"]["mean"],
            throughput_gb_s=stats["throughput_gb_s"]["mean"],
        ))

    return results


def estimate_amdahls_fraction(
    results: list[ScalingResult],
    zoom_level: str,
) -> float:
    """Estimate serial fraction from Amdahl's law.

    Uses least squares to fit: 1/S = f + (1-f)/n
    where S is speedup, f is serial fraction, n is workers.

    Args:
        results: Scaling results
        zoom_level: Zoom level to analyze

    Returns:
        Estimated serial fraction (0-1)
    """
    filtered = [r for r in results if r.zoom_level == zoom_level]
    if len(filtered) < 2:
        return 0.0

    # Extract data
    n = np.array([r.worker_count for r in filtered])
    s = np.array([r.speedup for r in filtered])

    # Fit 1/S = f + (1-f)/n
    # Rearrange: 1/S = f * (1 - 1/n) + 1/n
    # Let y = 1/S - 1/n, x = 1 - 1/n
    # Then y = f * x
    x = 1 - 1/n
    y = 1/s - 1/n

    # Least squares: f = sum(x*y) / sum(x*x)
    if np.sum(x * x) > 0:
        f = np.sum(x * y) / np.sum(x * x)
        return float(np.clip(f, 0, 1))

    return 0.0


def compute_strong_scaling_efficiency(
    results: list[ScalingResult],
    zoom_level: str,
) -> dict:
    """Compute strong scaling efficiency metrics.

    Strong scaling: fixed problem size, varying workers.

    Args:
        results: Scaling results
        zoom_level: Zoom level to analyze

    Returns:
        Dictionary with scaling metrics
    """
    filtered = [r for r in results if r.zoom_level == zoom_level]
    if not filtered:
        return {}

    workers = [r.worker_count for r in filtered]
    speedups = [r.speedup for r in filtered]
    efficiencies = [r.efficiency for r in filtered]

    return {
        "zoom_level": zoom_level,
        "worker_counts": workers,
        "speedups": speedups,
        "efficiencies": efficiencies,
        "max_speedup": max(speedups),
        "mean_efficiency": float(np.mean(efficiencies)),
        "amdahls_fraction": estimate_amdahls_fraction(filtered, zoom_level),
    }


def compute_weak_scaling_efficiency(
    results: list[ScalingResult],
    base_zoom: str = "full",
) -> dict:
    """Compute weak scaling efficiency metrics.

    Weak scaling: problem size proportional to workers.
    We approximate by comparing across zoom levels.

    Args:
        results: Scaling results
        base_zoom: Base zoom level

    Returns:
        Dictionary with scaling metrics
    """
    # This is an approximation since zoom != problem size
    # But it gives insight into behavior

    zoom_map = {"full": 1.0, "10pct": 0.1, "1pct": 0.01, "0.1pct": 0.001}

    data = {}
    for r in results:
        key = (r.worker_count, r.zoom_level)
        data[key] = r

    # For each worker count, compute time ratio vs baseline
    workers = sorted(set(r.worker_count for r in results))

    ratios = []
    for w in workers:
        if (w, base_zoom) in data:
            base_time = data[(w, base_zoom)].mean_time_s
            for zoom, frac in zoom_map.items():
                if (w, zoom) in data:
                    zoom_time = data[(w, zoom)].mean_time_s
                    expected_ratio = frac  # Ideally time scales with data
                    actual_ratio = zoom_time / base_time if base_time > 0 else 0
                    ratios.append({
                        "workers": w,
                        "zoom": zoom,
                        "expected_ratio": expected_ratio,
                        "actual_ratio": actual_ratio,
                    })

    return {
        "base_zoom": base_zoom,
        "time_ratios": ratios,
    }


@dataclass
class AnalysisSummary:
    """Complete analysis summary for a benchmark run.

    Attributes:
        run_name: Name of benchmark run
        total_metrics: Total number of metrics
        production_metrics: Number of non-warmup metrics
        scaling_results: Per-config scaling results
        strong_scaling: Strong scaling analysis per zoom
        amdahls_estimates: Amdahl's fraction per zoom
        recommendations: Auto-generated recommendations
    """
    run_name: str
    total_metrics: int
    production_metrics: int
    scaling_results: list[ScalingResult]
    strong_scaling: dict[str, dict]
    amdahls_estimates: dict[str, float]
    recommendations: list[str]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "run_name": self.run_name,
            "total_metrics": self.total_metrics,
            "production_metrics": self.production_metrics,
            "scaling_results": [r.to_dict() for r in self.scaling_results],
            "strong_scaling": self.strong_scaling,
            "amdahls_estimates": self.amdahls_estimates,
            "recommendations": self.recommendations,
        }


def analyze_run(run: BenchmarkRun) -> AnalysisSummary:
    """Perform complete analysis of a benchmark run.

    Args:
        run: Benchmark run to analyze

    Returns:
        AnalysisSummary with all results
    """
    scaling_results = compute_scaling_results(run)

    zoom_levels = sorted(set(r.zoom_level for r in scaling_results))

    strong_scaling = {}
    amdahls = {}

    for zoom in zoom_levels:
        strong_scaling[zoom] = compute_strong_scaling_efficiency(
            scaling_results, zoom
        )
        amdahls[zoom] = estimate_amdahls_fraction(scaling_results, zoom)

    # Generate recommendations
    recommendations = []

    # Check efficiency
    for zoom, metrics in strong_scaling.items():
        if metrics.get("mean_efficiency", 0) < 0.5:
            recommendations.append(
                f"Low efficiency ({metrics['mean_efficiency']:.2f}) at {zoom} zoom - "
                "consider reducing worker count"
            )

    # Check Amdahl's fraction
    for zoom, f in amdahls.items():
        if f > 0.1:
            recommendations.append(
                f"High serial fraction ({f:.2f}) at {zoom} zoom - "
                "parallelization is limited"
            )

    # Check for memory pressure
    prod_metrics = run.production_metrics
    high_memory = [m for m in prod_metrics if m.peak_memory_pct > 80]
    if high_memory:
        recommendations.append(
            f"{len(high_memory)} runs had >80% memory usage - "
            "consider adding workers or memory"
        )

    return AnalysisSummary(
        run_name=run.run_name,
        total_metrics=len(run.metrics),
        production_metrics=len(run.production_metrics),
        scaling_results=scaling_results,
        strong_scaling=strong_scaling,
        amdahls_estimates=amdahls,
        recommendations=recommendations or ["No issues detected"],
    )
