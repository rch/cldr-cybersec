"""Report generation for scaling benchmarks.

This module generates Markdown and JSON reports from benchmark results.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .analysis import AnalysisSummary, analyze_run, compute_summary_statistics
from .config import BenchmarkConfig
from .metrics import BenchmarkRun


def generate_markdown_report(
    run: BenchmarkRun,
    analysis: Optional[AnalysisSummary] = None,
    config: Optional[BenchmarkConfig] = None,
) -> str:
    """Generate Markdown report from benchmark run.

    Args:
        run: Benchmark run with metrics
        analysis: Optional pre-computed analysis
        config: Optional configuration

    Returns:
        Markdown string
    """
    if analysis is None:
        analysis = analyze_run(run)

    lines = []

    # Header
    lines.append(f"# Dask Scaling Benchmark Report: {run.run_name}")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total runs**: {analysis.total_metrics}")
    lines.append(f"- **Production runs**: {analysis.production_metrics}")
    if run.duration_seconds:
        lines.append(f"- **Duration**: {run.duration_seconds:.1f} seconds")
    lines.append("")

    # Configuration
    if config:
        lines.append("## Configuration")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        lines.append(f"| Total spans | {config.total_spans:,} |")
        lines.append(f"| Partitions | {config.partitions} |")
        lines.append(f"| Workers tested | {config.worker_counts} |")
        lines.append(f"| Zoom levels | {[z[0] for z in config.zoom_levels]} |")
        lines.append(f"| Runs per config | {config.runs_per_config} |")
        lines.append(f"| Random seed | {config.random_seed} |")
        lines.append("")

    # Scaling Results Table
    lines.append("## Scaling Results")
    lines.append("")
    lines.append("| Workers | Zoom | Time (s) | Speedup | Efficiency | Throughput (GB/s) |")
    lines.append("|---------|------|----------|---------|------------|-------------------|")

    for r in sorted(analysis.scaling_results, key=lambda x: (x.zoom_level, x.worker_count)):
        lines.append(
            f"| {r.worker_count} | {r.zoom_level} | "
            f"{r.mean_time_s:.2f} ± {r.std_time_s:.2f} | "
            f"{r.speedup:.2f} | {r.efficiency:.2f} | {r.throughput_gb_s:.2f} |"
        )
    lines.append("")

    # Amdahl's Law Analysis
    lines.append("## Amdahl's Law Analysis")
    lines.append("")
    lines.append("| Zoom Level | Serial Fraction | Max Theoretical Speedup |")
    lines.append("|------------|-----------------|-------------------------|")

    for zoom, f in analysis.amdahls_estimates.items():
        max_speedup = 1 / f if f > 0 else float('inf')
        lines.append(f"| {zoom} | {f:.3f} | {max_speedup:.1f}x |")
    lines.append("")

    # Strong Scaling
    lines.append("## Strong Scaling Efficiency")
    lines.append("")

    for zoom, metrics in analysis.strong_scaling.items():
        lines.append(f"### {zoom}")
        lines.append("")
        lines.append(f"- Max speedup: {metrics.get('max_speedup', 0):.2f}x")
        lines.append(f"- Mean efficiency: {metrics.get('mean_efficiency', 0):.2%}")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    for rec in analysis.recommendations:
        lines.append(f"- {rec}")
    lines.append("")

    # Environment
    if run.metadata.get("environment"):
        env = run.metadata["environment"]
        lines.append("## Environment")
        lines.append("")
        lines.append(f"- **Python**: {env.get('python_version', 'N/A')}")
        lines.append(f"- **Dask**: {env.get('dask_version', 'N/A')}")
        lines.append(f"- **Datashader**: {env.get('datashader_version', 'N/A')}")
        lines.append(f"- **NumPy**: {env.get('numpy_version', 'N/A')}")
        lines.append(f"- **CPU**: {env.get('cpu_model', 'N/A')} ({env.get('cpu_cores', 0)} cores)")
        lines.append(f"- **Memory**: {env.get('memory_total_gb', 0):.1f} GB")
        lines.append("")

    return "\n".join(lines)


def generate_json_report(
    run: BenchmarkRun,
    analysis: Optional[AnalysisSummary] = None,
    config: Optional[BenchmarkConfig] = None,
) -> str:
    """Generate JSON report from benchmark run.

    Args:
        run: Benchmark run with metrics
        analysis: Optional pre-computed analysis
        config: Optional configuration

    Returns:
        JSON string
    """
    if analysis is None:
        analysis = analyze_run(run)

    report = {
        "run_name": run.run_name,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_metrics": analysis.total_metrics,
            "production_metrics": analysis.production_metrics,
            "duration_seconds": run.duration_seconds,
        },
        "scaling_results": [r.to_dict() for r in analysis.scaling_results],
        "strong_scaling": analysis.strong_scaling,
        "amdahls_estimates": analysis.amdahls_estimates,
        "recommendations": analysis.recommendations,
        "environment": run.metadata.get("environment", {}),
    }

    if config:
        report["configuration"] = config.to_dict()

    return json.dumps(report, indent=2)


def generate_html_report(
    run: BenchmarkRun,
    analysis: Optional[AnalysisSummary] = None,
    config: Optional[BenchmarkConfig] = None,
    figure_dir: Optional[Path] = None,
) -> str:
    """Generate HTML report from benchmark run.

    Args:
        run: Benchmark run with metrics
        analysis: Optional pre-computed analysis
        config: Optional configuration
        figure_dir: Optional directory with figures to embed

    Returns:
        HTML string
    """
    if analysis is None:
        analysis = analyze_run(run)

    html = []
    html.append("<!DOCTYPE html>")
    html.append("<html>")
    html.append("<head>")
    html.append(f"<title>Dask Scaling Benchmark: {run.run_name}</title>")
    html.append("<style>")
    html.append("""
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }
        h1, h2, h3 { color: #333; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f5f5f5; }
        tr:nth-child(even) { background: #fafafa; }
        .metric { display: inline-block; padding: 10px 20px; margin: 5px; background: #f0f0f0; border-radius: 5px; }
        .metric-value { font-size: 24px; font-weight: bold; color: #2196F3; }
        .metric-label { font-size: 12px; color: #666; }
        .figure { max-width: 100%; margin: 20px 0; }
        .recommendation { padding: 10px; margin: 5px 0; background: #fff3e0; border-left: 4px solid #ff9800; }
    """)
    html.append("</style>")
    html.append("</head>")
    html.append("<body>")

    # Header
    html.append(f"<h1>Dask Scaling Benchmark Report: {run.run_name}</h1>")
    html.append(f"<p>Generated: {datetime.now().isoformat()}</p>")

    # Summary metrics
    html.append("<h2>Summary</h2>")
    html.append("<div>")
    html.append(f'<div class="metric"><div class="metric-value">{analysis.production_metrics}</div><div class="metric-label">Production Runs</div></div>')
    if run.duration_seconds:
        html.append(f'<div class="metric"><div class="metric-value">{run.duration_seconds:.0f}s</div><div class="metric-label">Duration</div></div>')
    if analysis.scaling_results:
        max_speedup = max(r.speedup for r in analysis.scaling_results)
        html.append(f'<div class="metric"><div class="metric-value">{max_speedup:.1f}x</div><div class="metric-label">Max Speedup</div></div>')
    html.append("</div>")

    # Scaling results table
    html.append("<h2>Scaling Results</h2>")
    html.append("<table>")
    html.append("<tr><th>Workers</th><th>Zoom</th><th>Time (s)</th><th>Speedup</th><th>Efficiency</th><th>Throughput (GB/s)</th></tr>")
    for r in sorted(analysis.scaling_results, key=lambda x: (x.zoom_level, x.worker_count)):
        html.append(
            f"<tr><td>{r.worker_count}</td><td>{r.zoom_level}</td>"
            f"<td>{r.mean_time_s:.2f} ± {r.std_time_s:.2f}</td>"
            f"<td>{r.speedup:.2f}</td><td>{r.efficiency:.2%}</td>"
            f"<td>{r.throughput_gb_s:.2f}</td></tr>"
        )
    html.append("</table>")

    # Figures
    if figure_dir and figure_dir.exists():
        html.append("<h2>Figures</h2>")
        for fig_path in sorted(figure_dir.glob("*.png")):
            html.append(f'<h3>{fig_path.stem}</h3>')
            # Embed as base64 for self-contained HTML
            import base64
            with open(fig_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode()
            html.append(f'<img class="figure" src="data:image/png;base64,{img_data}">')

    # Recommendations
    html.append("<h2>Recommendations</h2>")
    for rec in analysis.recommendations:
        html.append(f'<div class="recommendation">{rec}</div>')

    html.append("</body>")
    html.append("</html>")

    return "\n".join(html)


def save_report(
    run: BenchmarkRun,
    output_dir: Path,
    config: Optional[BenchmarkConfig] = None,
    format: str = "all",
) -> list[Path]:
    """Save reports in requested formats.

    Args:
        run: Benchmark run
        output_dir: Output directory
        config: Optional configuration
        format: "markdown", "json", "html", or "all"

    Returns:
        List of generated file paths
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis = analyze_run(run)

    files = []

    if format in ("markdown", "all"):
        md_path = output_dir / "report.md"
        md_path.write_text(generate_markdown_report(run, analysis, config))
        files.append(md_path)

    if format in ("json", "all"):
        json_path = output_dir / "report.json"
        json_path.write_text(generate_json_report(run, analysis, config))
        files.append(json_path)

    if format in ("html", "all"):
        fig_dir = output_dir / "figures"
        html_path = output_dir / "report.html"
        html_path.write_text(generate_html_report(run, analysis, config, fig_dir))
        files.append(html_path)

    return files
