#!/usr/bin/env python3
"""Dask Benchmark runner for AWS infrastructure.

This script runs the Dask/Datashader benchmark against an existing
Dask cluster deployed on AWS. It does NOT create a local cluster.

Usage:
    # Connect to existing scheduler
    python scripts/run_benchmark_aws.py --scheduler tcp://dask-scheduler:8786

    # Use environment variable
    DASK_SCHEDULER=tcp://dask-scheduler:8786 python scripts/run_benchmark_aws.py

    # Quick profile (12GB)
    python scripts/run_benchmark_aws.py --scheduler tcp://... --profile quick

    # Full benchmark (120GB)
    python scripts/run_benchmark_aws.py --scheduler tcp://... --profile full

Environment Variables:
    DASK_SCHEDULER: Dask scheduler address
    AWS_PROFILE: AWS profile for S3 access (default: "default")
    S3_BUCKET: Override S3 bucket name
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", message=".*coroutine.*was never awaited.*")

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_benchmark(scheduler_address: str, profile: str = "full", workers: list[int] = None):
    """Run benchmark against existing Dask cluster.

    Args:
        scheduler_address: Dask scheduler address (tcp://host:port)
        profile: Configuration profile ("full", "quick")
        workers: Override worker counts to test
    """
    from distributed import Client

    from cybersec.benchmarks.config import BenchmarkConfig, BENCHMARK_FULL, BENCHMARK_QUICK
    from cybersec.benchmarks.datashader_runner import DatashaderBenchmark
    from cybersec.benchmarks.metrics import BenchmarkRun
    from cybersec.benchmarks.analysis import analyze_run, compute_scaling_results
    from cybersec.benchmarks.report import generate_markdown_report
    from cybersec.benchmarks.figures import (
        create_scaling_heatmap,
        create_speedup_efficiency,
        create_throughput_vs_workers,
    )

    # Select configuration profile
    profiles = {
        "full": BENCHMARK_FULL,
        "quick": BENCHMARK_QUICK,
    }
    config = profiles.get(profile, BENCHMARK_FULL)

    # Override settings from environment
    if os.environ.get("S3_BUCKET"):
        config.s3_bucket = os.environ["S3_BUCKET"]
    if os.environ.get("AWS_PROFILE"):
        config.aws_profile = os.environ["AWS_PROFILE"]

    # Override worker counts if specified
    if workers:
        config.worker_counts = workers

    # For AWS, don't use endpoint (use real S3)
    config.s3_endpoint = None

    print(f"Dask Benchmark Runner (AWS)")
    print(f"=" * 60)
    print(f"Scheduler: {scheduler_address}")
    print(f"Profile: {profile}")
    print(f"Configuration:")
    print(f"  S3 Path: {config.s3_path}")
    print(f"  Total spans: {config.total_spans:,}")
    print(f"  Estimated size: {config.estimated_size_gb:.1f} GB")
    print(f"  Partitions: {config.partitions}")
    print(f"  Worker counts: {config.worker_counts}")
    print(f"  Zoom levels: {[z[0] for z in config.zoom_levels]}")
    print(f"  Runs per config: {config.runs_per_config}")
    print(f"  Total runs: {config.total_runs}")
    print()

    # Create output directory
    run_name = f"benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir = Path(config.output_dir) / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Run name: {run_name}")
    print(f"Output dir: {output_dir}")
    print()

    client = None
    try:
        # Connect to existing Dask cluster
        print(f"Connecting to Dask scheduler at {scheduler_address}...")
        client = Client(scheduler_address)

        # Show cluster info
        info = client.scheduler_info()
        n_workers = len(info.get('workers', {}))
        total_memory = sum(w.get('memory_limit', 0) for w in info['workers'].values())
        total_memory_gb = total_memory / (1024**3)

        print(f"  Workers: {n_workers}")
        print(f"  Total memory: {total_memory_gb:.1f} GB")
        print(f"  Dashboard: {client.dashboard_link}")
        print()

        # Verify we have enough workers
        max_workers_needed = max(config.worker_counts)
        if n_workers < max_workers_needed:
            print(f"WARNING: Cluster has {n_workers} workers, but benchmark needs up to {max_workers_needed}")
            print(f"         Adjusting worker_counts to available workers")
            config.worker_counts = [w for w in config.worker_counts if w <= n_workers]
            if not config.worker_counts:
                config.worker_counts = [n_workers]
            print(f"         New worker_counts: {config.worker_counts}")
            print()

        # Phase 1: Verify/generate dataset
        print("Phase 1: Verifying dataset...")
        from cybersec.benchmarks.executor import BenchmarkExecutor
        import asyncio

        executor = BenchmarkExecutor(config)

        # Run async verification
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            verify_result = loop.run_until_complete(executor.verify_dataset())
            if verify_result.get("valid"):
                print(f"  Dataset verified: {verify_result['partitions']} partitions")
            else:
                print(f"  Dataset not found or invalid: {verify_result.get('error')}")
                print(f"  Generating dataset...")
                gen_result = loop.run_until_complete(executor.generate_dataset())
                print(f"  {gen_result.get('message')}")
        finally:
            loop.close()
        print()

        # Phase 2: Run benchmarks
        print("Phase 2: Running benchmarks...")
        all_metrics = []

        ds_bench = DatashaderBenchmark(config)
        ds_bench.load_data()
        bounds = ds_bench.compute_bounds()

        run_id = 0
        for worker_count in config.worker_counts:
            print(f"\n  Testing with {worker_count} workers...")

            # Scale cluster if it supports adaptive scaling
            cluster = getattr(client, 'cluster', None)
            if cluster and hasattr(cluster, 'scale'):
                print(f"    Scaling cluster to {worker_count} workers...")
                cluster.scale(worker_count)
                client.wait_for_workers(worker_count, timeout=120)

            actual_workers = len(client.scheduler_info()['workers'])
            print(f"    Active workers: {actual_workers}")

            for zoom_name, zoom_fraction in config.zoom_levels:
                # Warmup runs
                for warmup_idx in range(config.warmup_runs):
                    print(f"    Warmup {warmup_idx + 1}/{config.warmup_runs} [{actual_workers}w, {zoom_name}]")
                    metrics = ds_bench.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=True,
                    )
                    all_metrics.append(metrics)
                    run_id += 1

                # Production runs
                for run_idx in range(config.runs_per_config):
                    print(f"    Run {run_idx + 1}/{config.runs_per_config} [{actual_workers}w, {zoom_name}]", end="")

                    metrics = ds_bench.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=False,
                    )

                    # Capture cluster state
                    info = client.scheduler_info()
                    workers_info = info.get('workers', {})
                    memory_used = sum(w.get('memory', 0) for w in workers_info.values())
                    memory_limit = sum(w.get('memory_limit', 0) for w in workers_info.values())
                    metrics.peak_memory_pct = 100 * memory_used / memory_limit if memory_limit else 0

                    wall_time_s = metrics.wall_clock_ns / 1e9
                    print(f" - {wall_time_s:.2f}s, {metrics.points_per_second/1e6:.2f}M pts/s, mem: {metrics.peak_memory_pct:.0f}%")

                    all_metrics.append(metrics)
                    run_id += 1

        print()
        print("Phase 3: Analyzing results...")

        # Create BenchmarkRun
        run = BenchmarkRun(
            config_hash=f"aws-{run_name}",
            run_name=run_name,
            metadata={
                "config": config.to_dict(),
                "scheduler": scheduler_address,
                "cluster_workers": n_workers,
                "cluster_memory_gb": total_memory_gb,
            },
        )
        for m in all_metrics:
            run.add_metric(m)
        run.mark_complete()

        # Analyze
        analysis = analyze_run(run)
        scaling_results = compute_scaling_results(run)

        # Save results
        run.save(output_dir / "run.json")
        config.save(output_dir / "config.toml")

        with open(output_dir / "analysis.json", 'w') as f:
            json.dump(analysis.to_dict(), f, indent=2)

        report = generate_markdown_report(run, analysis, config)
        with open(output_dir / "report.md", 'w') as f:
            f.write(report)

        print(f"  Analysis saved to {output_dir / 'analysis.json'}")
        print(f"  Report saved to {output_dir / 'report.md'}")

        # Generate figures
        print()
        print("Phase 4: Generating figures...")
        try:
            figures_dir = output_dir / "figures"
            figures_dir.mkdir(parents=True, exist_ok=True)
            create_scaling_heatmap(scaling_results, metric="time", output_path=figures_dir / "scaling_heatmap.png")
            create_speedup_efficiency(scaling_results, output_path=figures_dir / "speedup_efficiency.png")
            create_throughput_vs_workers(scaling_results, output_path=figures_dir / "throughput_vs_workers.png")
            print(f"  Figures saved to {figures_dir}")
        except Exception as e:
            print(f"  Figure generation failed: {e}")

        # Print summary
        print()
        print("=" * 60)
        print("BENCHMARK COMPLETE")
        print("=" * 60)
        print()
        print("Scaling Results:")
        print("-" * 70)
        print(f"{'Workers':<10} {'Zoom':<10} {'Time (s)':<15} {'Speedup':<10} {'Efficiency':<12} {'GB/s':<10}")
        print("-" * 70)
        for sr in scaling_results:
            print(f"{sr.worker_count:<10} {sr.zoom_level:<10} {sr.mean_time_s:.2f} ± {sr.std_time_s:.2f}     {sr.speedup:.2f}       {sr.efficiency:.2f}         {sr.throughput_gb_s:.2f}")

        print()
        print("Amdahl's Law Analysis:")
        print("-" * 60)
        for zoom, serial_fraction in analysis.amdahls_estimates.items():
            max_speedup = 1.0 / serial_fraction if serial_fraction > 0 else float('inf')
            print(f"  {zoom}: Serial fraction = {serial_fraction:.3f}, Max speedup = {max_speedup:.1f}x")

        print()
        print("Recommendations:")
        print("-" * 60)
        for rec in analysis.recommendations:
            print(f"  - {rec}")

        print()
        print(f"Full results: {output_dir}")

        return {
            "success": True,
            "run_name": run_name,
            "output_dir": str(output_dir),
            "metrics_count": len(all_metrics),
            "scaling_results": len(scaling_results),
        }

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

    finally:
        if client is not None:
            print()
            print("Disconnecting from cluster...")
            client.close()
        print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Run Dask scaling benchmark on AWS Dask cluster",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scheduler", "-s",
        default=os.environ.get("DASK_SCHEDULER"),
        help="Dask scheduler address (tcp://host:port)",
    )
    parser.add_argument(
        "--profile", "-p",
        choices=["full", "quick"],
        default="full",
        help="Benchmark profile: full (120GB) or quick (12GB)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=lambda s: [int(x) for x in s.split(",")],
        help="Override worker counts (comma-separated, e.g., '2,4,8,16')",
    )

    args = parser.parse_args()

    if not args.scheduler:
        print("ERROR: Dask scheduler address required")
        print("       Use --scheduler tcp://host:port or set DASK_SCHEDULER env var")
        sys.exit(1)

    result = run_benchmark(
        scheduler_address=args.scheduler,
        profile=args.profile,
        workers=args.workers,
    )
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
