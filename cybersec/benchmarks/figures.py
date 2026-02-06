"""Publication-quality figure generation for scaling benchmarks.

This module creates matplotlib figures following SC paper styling
guidelines for the scaling benchmark results.
"""

from pathlib import Path
from typing import Optional

import numpy as np

from .analysis import AnalysisSummary, compute_scaling_results
from .metrics import BenchmarkRun, ScalingResult


# Publication paper styling
SC_STYLE = {
    "figure.figsize": (6, 4),
    "font.size": 10,
    "font.family": "serif",
    "axes.labelsize": 10,
    "axes.titlesize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "lines.linewidth": 1.5,
    "lines.markersize": 6,
    "axes.grid": True,
    "grid.alpha": 0.3,
}

# Color palette for different zoom levels
ZOOM_COLORS = {
    "full": "#1f77b4",    # Blue
    "10pct": "#ff7f0e",   # Orange
    "1pct": "#2ca02c",    # Green
    "0.1pct": "#d62728",  # Red
}

# Markers for different worker counts
WORKER_MARKERS = {
    2: "o",
    4: "s",
    8: "^",
    16: "D",
    32: "v",
}


def apply_sc_style():
    """Apply SC paper styling to matplotlib."""
    import matplotlib.pyplot as plt
    plt.rcParams.update(SC_STYLE)


def create_scaling_heatmap(
    results: list[ScalingResult],
    metric: str = "time",
    output_path: Optional[Path] = None,
) -> None:
    """Create heatmap of scaling results.

    Args:
        results: List of scaling results
        metric: Metric to plot ("time", "speedup", "efficiency", "throughput")
        output_path: Optional path to save figure
    """
    import matplotlib.pyplot as plt

    apply_sc_style()

    # Extract unique workers and zooms
    workers = sorted(set(r.worker_count for r in results))
    zooms = sorted(set(r.zoom_level for r in results))

    # Build data matrix
    data = np.zeros((len(zooms), len(workers)))
    for r in results:
        i = zooms.index(r.zoom_level)
        j = workers.index(r.worker_count)

        if metric == "time":
            data[i, j] = r.mean_time_s
        elif metric == "speedup":
            data[i, j] = r.speedup
        elif metric == "efficiency":
            data[i, j] = r.efficiency
        elif metric == "throughput":
            data[i, j] = r.throughput_gb_s

    fig, ax = plt.subplots(figsize=(8, 5))

    # Create heatmap
    cmap = "viridis" if metric in ("speedup", "throughput") else "viridis_r"
    im = ax.imshow(data, cmap=cmap, aspect='auto')

    # Labels
    ax.set_xticks(range(len(workers)))
    ax.set_xticklabels(workers)
    ax.set_yticks(range(len(zooms)))
    ax.set_yticklabels(zooms)

    ax.set_xlabel("Workers")
    ax.set_ylabel("Zoom Level")

    titles = {
        "time": "Mean Time (seconds)",
        "speedup": "Speedup",
        "efficiency": "Parallel Efficiency",
        "throughput": "Throughput (GB/s)",
    }
    ax.set_title(titles.get(metric, metric))

    # Colorbar
    cbar = fig.colorbar(im, ax=ax)

    # Annotate cells
    for i in range(len(zooms)):
        for j in range(len(workers)):
            value = data[i, j]
            if metric == "time":
                text = f"{value:.1f}s"
            elif metric in ("speedup", "efficiency"):
                text = f"{value:.2f}"
            else:
                text = f"{value:.2f}"
            ax.text(j, i, text, ha="center", va="center", fontsize=8)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def create_throughput_vs_workers(
    results: list[ScalingResult],
    output_path: Optional[Path] = None,
) -> None:
    """Create throughput vs workers plot.

    Args:
        results: List of scaling results
        output_path: Optional path to save figure
    """
    import matplotlib.pyplot as plt

    apply_sc_style()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    zooms = sorted(set(r.zoom_level for r in results))

    # Plot 1: Throughput (points/s)
    for zoom in zooms:
        zoom_results = [r for r in results if r.zoom_level == zoom]
        workers = [r.worker_count for r in zoom_results]
        throughput = [r.throughput_pts_s / 1e6 for r in zoom_results]  # M pts/s

        color = ZOOM_COLORS.get(zoom, "gray")
        ax1.plot(workers, throughput, 'o-', label=zoom, color=color)

    ax1.set_xlabel("Workers")
    ax1.set_ylabel("Throughput (M points/s)")
    ax1.set_title("Point Processing Throughput")
    ax1.legend()
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log")

    # Plot 2: Throughput (GB/s)
    for zoom in zooms:
        zoom_results = [r for r in results if r.zoom_level == zoom]
        workers = [r.worker_count for r in zoom_results]
        throughput = [r.throughput_gb_s for r in zoom_results]

        color = ZOOM_COLORS.get(zoom, "gray")
        ax2.plot(workers, throughput, 'o-', label=zoom, color=color)

    ax2.set_xlabel("Workers")
    ax2.set_ylabel("Throughput (GB/s)")
    ax2.set_title("Data Throughput")
    ax2.legend()
    ax2.set_xscale("log", base=2)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def create_speedup_efficiency(
    results: list[ScalingResult],
    output_path: Optional[Path] = None,
) -> None:
    """Create speedup and efficiency plot.

    Args:
        results: List of scaling results
        output_path: Optional path to save figure
    """
    import matplotlib.pyplot as plt

    apply_sc_style()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    zooms = sorted(set(r.zoom_level for r in results))
    workers = sorted(set(r.worker_count for r in results))

    # Ideal speedup line
    ax1.plot(workers, workers, 'k--', label='Ideal', alpha=0.5)

    # Plot 1: Speedup
    for zoom in zooms:
        zoom_results = [r for r in results if r.zoom_level == zoom]
        w = [r.worker_count for r in zoom_results]
        s = [r.speedup for r in zoom_results]

        color = ZOOM_COLORS.get(zoom, "gray")
        ax1.plot(w, s, 'o-', label=zoom, color=color)

    ax1.set_xlabel("Workers")
    ax1.set_ylabel("Speedup")
    ax1.set_title("Strong Scaling Speedup")
    ax1.legend()
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log", base=2)

    # Ideal efficiency line
    ax2.axhline(y=1.0, color='k', linestyle='--', label='Ideal', alpha=0.5)

    # Plot 2: Efficiency
    for zoom in zooms:
        zoom_results = [r for r in results if r.zoom_level == zoom]
        w = [r.worker_count for r in zoom_results]
        e = [r.efficiency for r in zoom_results]

        color = ZOOM_COLORS.get(zoom, "gray")
        ax2.plot(w, e, 'o-', label=zoom, color=color)

    ax2.set_xlabel("Workers")
    ax2.set_ylabel("Efficiency")
    ax2.set_title("Parallel Efficiency")
    ax2.legend()
    ax2.set_xscale("log", base=2)
    ax2.set_ylim(0, 1.1)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def create_memory_pressure(
    run: BenchmarkRun,
    output_path: Optional[Path] = None,
) -> None:
    """Create memory pressure visualization.

    Args:
        run: Benchmark run with metrics
        output_path: Optional path to save figure
    """
    import matplotlib.pyplot as plt

    apply_sc_style()

    metrics = run.production_metrics

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Group by worker count
    workers = sorted(set(m.worker_count for m in metrics))

    # Plot 1: Memory usage by worker count
    for w in workers:
        worker_metrics = [m for m in metrics if m.worker_count == w]
        runs = range(len(worker_metrics))
        memory = [m.peak_memory_pct for m in worker_metrics]

        ax1.plot(runs, memory, 'o-', label=f"{w} workers", alpha=0.7)

    ax1.set_xlabel("Run")
    ax1.set_ylabel("Peak Memory (%)")
    ax1.set_title("Memory Pressure Over Runs")
    ax1.legend()
    ax1.axhline(y=80, color='r', linestyle='--', alpha=0.5, label='Warning')

    # Plot 2: Boxplot by worker count
    data = []
    labels = []
    for w in workers:
        worker_metrics = [m for m in metrics if m.worker_count == w]
        data.append([m.peak_memory_pct for m in worker_metrics])
        labels.append(str(w))

    ax2.boxplot(data, labels=labels)
    ax2.set_xlabel("Workers")
    ax2.set_ylabel("Peak Memory (%)")
    ax2.set_title("Memory Distribution by Worker Count")
    ax2.axhline(y=80, color='r', linestyle='--', alpha=0.5)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def create_s3_throughput(
    run: BenchmarkRun,
    output_path: Optional[Path] = None,
) -> None:
    """Create S3 I/O throughput visualization.

    Args:
        run: Benchmark run with metrics
        output_path: Optional path to save figure
    """
    import matplotlib.pyplot as plt

    apply_sc_style()

    metrics = run.production_metrics

    fig, ax = plt.subplots(figsize=(8, 5))

    zooms = sorted(set(m.zoom_level for m in metrics))
    workers = sorted(set(m.worker_count for m in metrics))

    for zoom in zooms:
        zoom_metrics = sorted(
            [m for m in metrics if m.zoom_level == zoom],
            key=lambda m: m.worker_count
        )

        # Average by worker count
        w_list = []
        t_list = []
        for w in workers:
            w_metrics = [m for m in zoom_metrics if m.worker_count == w]
            if w_metrics:
                w_list.append(w)
                t_list.append(np.mean([m.s3_throughput_mbps for m in w_metrics]))

        color = ZOOM_COLORS.get(zoom, "gray")
        ax.plot(w_list, t_list, 'o-', label=zoom, color=color)

    ax.set_xlabel("Workers")
    ax.set_ylabel("S3 Throughput (MB/s)")
    ax.set_title("S3 Read Throughput")
    ax.legend()
    ax.set_xscale("log", base=2)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def generate_all_figures(
    run: BenchmarkRun,
    output_dir: Path,
) -> list[Path]:
    """Generate all publication figures.

    Args:
        run: Benchmark run with metrics
        output_dir: Directory to save figures

    Returns:
        List of generated figure paths
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    results = compute_scaling_results(run)

    figures = []

    # Scaling heatmap
    for metric in ["time", "speedup", "efficiency", "throughput"]:
        path = output_dir / f"scaling_heatmap_{metric}.png"
        create_scaling_heatmap(results, metric=metric, output_path=path)
        figures.append(path)

    # Throughput vs workers
    path = output_dir / "throughput_vs_workers.png"
    create_throughput_vs_workers(results, output_path=path)
    figures.append(path)

    # Speedup and efficiency
    path = output_dir / "speedup_efficiency.png"
    create_speedup_efficiency(results, output_path=path)
    figures.append(path)

    # Memory pressure
    path = output_dir / "memory_pressure.png"
    create_memory_pressure(run, output_path=path)
    figures.append(path)

    # S3 throughput (only if data available)
    if any(m.s3_throughput_mbps > 0 for m in run.production_metrics):
        path = output_dir / "s3_throughput.png"
        create_s3_throughput(run, output_path=path)
        figures.append(path)

    return figures
