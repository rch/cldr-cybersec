"""Benchmark commands for Dask/Datashader scaling benchmarks.

Commands:
    /benchmark generate         - Generate benchmark dataset
    /benchmark run              - Run scaling benchmarks
    /benchmark analyze          - Analyze results and generate figures
    /benchmark suite             - Full benchmark suite

IMPORTANT: These commands require a Dask scheduler running on AWS infrastructure.
Set DASK_SCHEDULER environment variable or use --scheduler option.
Local clusters are not supported.
"""

import os
from pathlib import Path

from .parser import ParsedCommand, CommandResult
from .registry import register_command


def _get_scheduler_address(cmd: ParsedCommand) -> str:
    """Get Dask scheduler address from options or environment.

    Args:
        cmd: Parsed command with options

    Returns:
        Scheduler address string

    Raises:
        ValueError: If no scheduler configured
    """
    scheduler = cmd.options.get("scheduler") or os.environ.get("DASK_SCHEDULER")
    if not scheduler:
        raise ValueError(
            "Dask scheduler required. Use --scheduler tcp://host:port "
            "or set DASK_SCHEDULER environment variable. "
            "Local clusters are not supported - deploy Dask on AWS infrastructure."
        )
    return scheduler


async def cmd_benchmark_generate(cmd: ParsedCommand) -> CommandResult:
    """Generate 120GB benchmark dataset.

    Generates seeded OTEL span data to S3 for reproducible benchmarking.

    Options:
        --dry-run       Show what would be done without doing it
        --force         Force regeneration even if exists
        --profile <p>   Configuration profile (full, quick, local)
        --json, -j      Output as JSON
    """
    from ..benchmarks.config import BenchmarkConfig, BENCHMARK_FULL, BENCHMARK_QUICK
    from ..benchmarks.executor import BenchmarkExecutor

    dry_run = cmd.options.get("dry_run", False)
    force = cmd.options.get("force", False)
    profile = cmd.options.get("profile", "full")

    # Select configuration profile
    profiles = {
        "full": BENCHMARK_FULL,
        "quick": BENCHMARK_QUICK,
    }
    config = profiles.get(profile, BENCHMARK_FULL)

    # Override with S3 options if provided
    if cmd.options.get("s3_endpoint"):
        config.s3_endpoint = cmd.options["s3_endpoint"]
    if cmd.options.get("s3_bucket"):
        config.s3_bucket = cmd.options["s3_bucket"]

    # Validate configuration
    errors = config.validate()
    if errors:
        return CommandResult(
            success=False,
            error=f"Invalid configuration: {'; '.join(errors)}",
        )

    # Note: generate doesn't need scheduler, just S3 access
    executor = BenchmarkExecutor(config)

    try:
        result = await executor.generate_dataset(
            force=force,
            dry_run=dry_run,
        )

        if result["status"] == "dry_run":
            formatted = _format_generate_preview(result, config)
        elif result["status"] == "exists":
            formatted = _format_dataset_exists(result)
        else:
            formatted = _format_generate_complete(result)

        return CommandResult(
            success=True,
            data=result,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Dataset generation failed: {e}",
        )


async def cmd_benchmark_run(cmd: ParsedCommand) -> CommandResult:
    """Run scaling benchmarks.

    Runs full benchmark suite with cluster scaling and metric collection.
    REQUIRES: Dask scheduler running on AWS infrastructure.

    Options:
        --scheduler <s> Dask scheduler address (tcp://host:port)
        --workers <w>   Worker counts (comma-separated, e.g., "2,4,8")
        --zooms <z>     Zoom levels (comma-separated, e.g., "full,10pct")
        --runs <n>      Runs per configuration (default: 10)
        --profile <p>   Configuration profile (full, quick, local)
        --json, -j      Output as JSON
    """
    from ..benchmarks.config import BENCHMARK_FULL, BENCHMARK_QUICK
    from ..benchmarks.executor import BenchmarkExecutor

    # Require scheduler address
    try:
        scheduler = _get_scheduler_address(cmd)
    except ValueError as e:
        return CommandResult(success=False, error=str(e))

    profile = cmd.options.get("profile", "quick")  # Default to quick for safety

    profiles = {
        "full": BENCHMARK_FULL,
        "quick": BENCHMARK_QUICK,
    }
    config = profiles.get(profile, BENCHMARK_QUICK)

    # Parse worker counts
    workers = None
    if cmd.options.get("workers"):
        workers = [int(w.strip()) for w in cmd.options["workers"].split(",")]

    # Parse zoom levels
    zooms = None
    if cmd.options.get("zooms"):
        zooms = [z.strip() for z in cmd.options["zooms"].split(",")]

    # Parse runs
    runs = None
    if cmd.options.get("runs"):
        runs = int(cmd.options["runs"])

    executor = BenchmarkExecutor(config, scheduler_address=scheduler)

    # Progress updates
    progress_lines = []

    def on_progress(message: str, percent: float):
        progress_lines.append(f"[{percent:.0f}%] {message}")

    executor.progress_callback = on_progress

    try:
        run = await executor.run_benchmarks(
            workers=workers,
            zooms=zooms,
            runs=runs,
        )

        data = {
            "run_name": run.run_name,
            "total_metrics": len(run.metrics),
            "production_metrics": len(run.production_metrics),
            "duration_seconds": run.duration_seconds,
            "output_dir": str(config.get_run_output_dir(run.run_name)),
        }

        formatted = _format_run_complete(run, progress_lines)

        return CommandResult(
            success=True,
            data=data,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Benchmark run failed: {e}",
        )


async def cmd_benchmark_analyze(cmd: ParsedCommand) -> CommandResult:
    """Analyze results and generate figures.

    Analyzes a benchmark run and generates publication-quality figures.

    Args:
        <path>          Path to benchmark run directory or run.json

    Options:
        --format <f>    Output format: markdown, json, html, all (default: all)
        --figures       Generate figures (default: true)
        --json, -j      Output as JSON
    """
    from ..benchmarks.analysis import analyze_run
    from ..benchmarks.config import BenchmarkConfig
    from ..benchmarks.figures import generate_all_figures
    from ..benchmarks.metrics import BenchmarkRun
    from ..benchmarks.report import save_report

    if not cmd.args:
        return CommandResult(
            success=False,
            error="Missing required argument: path to benchmark run",
        )

    run_path = Path(cmd.args[0])

    # Handle both directory and file paths
    if run_path.is_dir():
        run_file = run_path / "run.json"
        config_file = run_path / "config.toml"
    else:
        run_file = run_path
        config_file = run_path.parent / "config.toml"

    if not run_file.exists():
        return CommandResult(
            success=False,
            error=f"Run file not found: {run_file}",
        )

    # Load run and config
    run = BenchmarkRun.load(run_file)
    config = None
    if config_file.exists():
        config = BenchmarkConfig.load(config_file)

    # Analyze
    analysis = analyze_run(run)

    # Output directory
    output_dir = run_path if run_path.is_dir() else run_path.parent

    # Generate figures
    figures = []
    if cmd.options.get("figures", True):
        fig_dir = output_dir / "figures"
        figures = generate_all_figures(run, fig_dir)

    # Save reports
    format_opt = cmd.options.get("format", "all")
    reports = save_report(run, output_dir, config, format=format_opt)

    data = {
        "run_name": run.run_name,
        "analysis": analysis.to_dict(),
        "figures": [str(f) for f in figures],
        "reports": [str(r) for r in reports],
    }

    formatted = _format_analysis_complete(analysis, figures, reports)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


async def cmd_benchmark_suite(cmd: ParsedCommand) -> CommandResult:
    """Full benchmark suite.

    Runs complete benchmark: generate dataset, run benchmarks, analyze results.
    REQUIRES: Dask scheduler running on AWS infrastructure.

    Options:
        --scheduler <s> Dask scheduler address (tcp://host:port)
        --profile <p>   Configuration profile (full, quick, local)
        --skip-generate Skip dataset generation
        --skip-run      Skip benchmark run
        --json, -j      Output as JSON
    """
    from ..benchmarks.config import BENCHMARK_FULL, BENCHMARK_QUICK
    from ..benchmarks.executor import BenchmarkExecutor
    from ..benchmarks.analysis import analyze_run
    from ..benchmarks.figures import generate_all_figures
    from ..benchmarks.report import save_report

    # Require scheduler address
    try:
        scheduler = _get_scheduler_address(cmd)
    except ValueError as e:
        return CommandResult(success=False, error=str(e))

    profile = cmd.options.get("profile", "quick")
    skip_generate = cmd.options.get("skip_generate", False)
    skip_run = cmd.options.get("skip_run", False)

    profiles = {
        "full": BENCHMARK_FULL,
        "quick": BENCHMARK_QUICK,
    }
    config = profiles.get(profile, BENCHMARK_QUICK)

    executor = BenchmarkExecutor(config, scheduler_address=scheduler)

    steps_completed = []
    errors = []

    # Step 1: Generate dataset
    if not skip_generate:
        try:
            gen_result = await executor.generate_dataset()
            steps_completed.append(f"Dataset: {gen_result['status']}")
        except Exception as e:
            errors.append(f"Generation failed: {e}")

    # Step 2: Run benchmarks
    run = None
    if not skip_run and not errors:
        try:
            run = await executor.run_benchmarks()
            steps_completed.append(f"Benchmark: {run.run_name}")
        except Exception as e:
            errors.append(f"Benchmark failed: {e}")

    # Step 3: Analyze and generate figures
    if run and not errors:
        try:
            analysis = analyze_run(run)
            output_dir = config.get_run_output_dir(run.run_name)

            figures = generate_all_figures(run, output_dir / "figures")
            reports = save_report(run, output_dir, config)

            steps_completed.append(f"Analysis: {len(figures)} figures, {len(reports)} reports")
        except Exception as e:
            errors.append(f"Analysis failed: {e}")

    if errors:
        return CommandResult(
            success=False,
            error="; ".join(errors),
            data={"steps_completed": steps_completed},
        )

    data = {
        "steps_completed": steps_completed,
        "run_name": run.run_name if run else None,
        "output_dir": str(config.get_run_output_dir(run.run_name)) if run else None,
    }

    formatted = _format_suite_complete(data)

    return CommandResult(
        success=True,
        data=data,
        formatted=formatted,
    )


# === Formatting helpers ===

def _format_generate_preview(result: dict, config) -> str:
    """Format generation preview."""
    lines = []
    lines.append("Benchmark Dataset Generation (DRY RUN)")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Would generate {config.partitions:,} partitions")
    lines.append(f"Total spans: {config.total_spans:,}")
    lines.append(f"Estimated size: {config.estimated_size_gb:.1f} GB")
    lines.append(f"Destination: {result['path']}")
    lines.append("")
    lines.append("To generate:")
    lines.append("  /benchmark generate")
    return "\n".join(lines)


def _format_dataset_exists(result: dict) -> str:
    """Format existing dataset message."""
    lines = []
    lines.append("Dataset Already Exists")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Path: {result['path']}")
    lines.append(f"Hash: {result['dataset_hash']}")
    lines.append("")
    lines.append("Use --force to regenerate")
    return "\n".join(lines)


def _format_generate_complete(result: dict) -> str:
    """Format generation complete message."""
    lines = []
    lines.append("Dataset Generation Complete")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"✓ Generated {result.get('message', 'dataset')}")
    lines.append(f"  Path: {result['path']}")
    lines.append(f"  Hash: {result['dataset_hash']}")
    if result.get("total_spans"):
        lines.append(f"  Total spans: {result['total_spans']:,}")
    return "\n".join(lines)


def _format_run_complete(run, progress_lines: list) -> str:
    """Format benchmark run complete message."""
    lines = []
    lines.append("Benchmark Run Complete")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Run: {run.run_name}")
    lines.append(f"Total metrics: {len(run.metrics)}")
    lines.append(f"Production metrics: {len(run.production_metrics)}")
    if run.duration_seconds:
        lines.append(f"Duration: {run.duration_seconds:.1f}s")
    lines.append("")

    # Summary of last few progress lines
    if progress_lines:
        lines.append("Progress:")
        for line in progress_lines[-5:]:
            lines.append(f"  {line}")
    return "\n".join(lines)


def _format_analysis_complete(analysis, figures: list, reports: list) -> str:
    """Format analysis complete message."""
    lines = []
    lines.append("Analysis Complete")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Run: {analysis.run_name}")
    lines.append(f"Production runs: {analysis.production_metrics}")
    lines.append("")

    lines.append("Scaling Summary:")
    for r in analysis.scaling_results[:5]:  # First 5
        lines.append(f"  {r.worker_count}w {r.zoom_level}: {r.speedup:.2f}x speedup")
    if len(analysis.scaling_results) > 5:
        lines.append(f"  ... and {len(analysis.scaling_results) - 5} more")
    lines.append("")

    lines.append("Recommendations:")
    for rec in analysis.recommendations:
        lines.append(f"  • {rec}")
    lines.append("")

    lines.append(f"Generated {len(figures)} figures, {len(reports)} reports")
    return "\n".join(lines)


def _format_suite_complete(data: dict) -> str:
    """Format Dask suite complete message."""
    lines = []
    lines.append("Benchmark Suite Complete")
    lines.append("=" * 50)
    lines.append("")

    for step in data.get("steps_completed", []):
        lines.append(f"✓ {step}")
    lines.append("")

    if data.get("output_dir"):
        lines.append(f"Results: {data['output_dir']}")
    return "\n".join(lines)


# === Registration ===

def register_benchmark_commands():
    """Register all benchmark commands."""
    register_command(
        "benchmark.generate",
        cmd_benchmark_generate,
        description="Generate benchmark dataset",
        options=[
            {"name": "dry-run", "description": "Preview without generating"},
            {"name": "force", "description": "Force regeneration"},
            {"name": "profile", "description": "Config profile (full, quick, local)"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/benchmark generate",
            "/benchmark generate --dry-run",
            "/benchmark generate --profile quick",
        ],
    )

    register_command(
        "benchmark.run",
        cmd_benchmark_run,
        description="Run scaling benchmarks (requires AWS Dask scheduler)",
        options=[
            {"name": "scheduler", "short": "s", "description": "Dask scheduler address (tcp://host:port)"},
            {"name": "workers", "description": "Worker counts (comma-separated)"},
            {"name": "zooms", "description": "Zoom levels (comma-separated)"},
            {"name": "runs", "description": "Runs per configuration"},
            {"name": "profile", "description": "Config profile (full, quick)"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/benchmark run --scheduler tcp://dask-scheduler:8786",
            "/benchmark run -s tcp://scheduler:8786 --workers 2,4,8,16",
            "/benchmark run --scheduler $DASK_SCHEDULER --profile full",
        ],
    )

    register_command(
        "benchmark.analyze",
        cmd_benchmark_analyze,
        description="Analyze results and generate figures",
        args=[
            {"name": "path", "required": True, "description": "Path to benchmark run"},
        ],
        options=[
            {"name": "format", "description": "Output format (markdown, json, html, all)"},
            {"name": "figures", "description": "Generate figures (default: true)"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/benchmark analyze build/benchmarks/benchmark-20240115-120000",
            "/benchmark analyze build/benchmarks/run.json --format markdown",
        ],
    )

    register_command(
        "benchmark.suite",
        cmd_benchmark_suite,
        description="Full benchmark suite (requires AWS Dask scheduler)",
        options=[
            {"name": "scheduler", "short": "s", "description": "Dask scheduler address (tcp://host:port)"},
            {"name": "profile", "description": "Config profile (full, quick)"},
            {"name": "skip-generate", "description": "Skip dataset generation"},
            {"name": "skip-run", "description": "Skip benchmark run"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/benchmark suite --scheduler tcp://dask-scheduler:8786",
            "/benchmark suite -s tcp://scheduler:8786 --profile full",
            "/benchmark suite --scheduler $DASK_SCHEDULER --skip-generate",
        ],
    )
