"""Benchmark orchestration for Dask/Datashader scaling study.

This module coordinates the full benchmark execution including
dataset generation, cluster scaling, and metric collection.

IMPORTANT: Requires external Dask scheduler on AWS infrastructure.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from .config import BenchmarkConfig
from .dataset import DatasetGenerator, compute_dataset_hash
from .datashader_runner import DatashaderBenchmark
from .metrics import BenchmarkMetrics, BenchmarkRun, EnvironmentMetadata
from .scaling import ClusterManager, get_cluster_state

logger = logging.getLogger(__name__)


class BenchmarkExecutor:
    """Orchestrates full benchmark execution.

    Handles:
    - Dataset generation and upload
    - Cluster scaling
    - Benchmark iteration execution
    - Metric collection and persistence

    Requires an external Dask scheduler - does not create local clusters.
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        scheduler_address: str = None,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ):
        """Initialize executor.

        Args:
            config: Benchmark configuration
            scheduler_address: Dask scheduler address (tcp://host:port).
                              Can also be set via DASK_SCHEDULER env var.
            progress_callback: Optional callback(message, percent) for progress
        """
        self.config = config
        self.scheduler_address = scheduler_address
        self.progress_callback = progress_callback
        self._dataset_generator = DatasetGenerator(config)
        self._cluster_manager = ClusterManager(config, scheduler_address)
        self._ds_benchmark = None

    def _report_progress(self, message: str, percent: float) -> None:
        """Report progress if callback is set."""
        if self.progress_callback:
            self.progress_callback(message, percent)
        logger.info(f"[{percent:.0f}%] {message}")

    async def generate_dataset(
        self,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Generate and upload benchmark dataset to S3.

        Args:
            force: Force regeneration even if exists
            dry_run: Show what would be done without doing it

        Returns:
            Dictionary with generation results
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        import s3fs

        config = self.config
        gen = self._dataset_generator

        # Setup S3 filesystem
        s3_options = {}
        if config.s3_endpoint:
            s3_options["client_kwargs"] = {"endpoint_url": config.s3_endpoint}
        if config.aws_access_key_id:
            s3_options["key"] = config.aws_access_key_id
            s3_options["secret"] = config.aws_secret_access_key
        elif config.aws_profile:
            s3_options["profile"] = config.aws_profile

        fs = s3fs.S3FileSystem(**s3_options) if s3_options else s3fs.S3FileSystem()

        # Check if dataset exists
        metadata_path = f"{config.s3_bucket}/{config.s3_prefix}/_metadata.json"
        exists = fs.exists(metadata_path)

        if exists and not force:
            # Load and verify existing metadata
            with fs.open(metadata_path, 'r') as f:
                existing_meta = json.load(f)

            if existing_meta.get("dataset_hash") == gen.dataset_hash:
                return {
                    "status": "exists",
                    "message": "Dataset already exists with matching hash",
                    "dataset_hash": gen.dataset_hash,
                    "path": config.s3_path,
                }

        if dry_run:
            return {
                "status": "dry_run",
                "message": f"Would generate {config.partitions} partitions",
                "total_spans": config.total_spans,
                "estimated_size_gb": config.estimated_size_gb,
                "path": config.s3_path,
            }

        # Generate partitions
        self._report_progress("Starting dataset generation", 0)

        generated_parts = 0
        for partition_id in range(config.partitions):
            df = gen.generate_partition(partition_id)

            # Write to S3
            part_path = f"{config.s3_bucket}/{config.s3_prefix}/part-{partition_id:05d}.parquet"
            with fs.open(part_path, 'wb') as f:
                pq.write_table(
                    df.to_arrow() if hasattr(df, 'to_arrow') else pa.Table.from_pandas(df),
                    f,
                    compression='snappy',
                )

            generated_parts += 1
            percent = 100 * generated_parts / config.partitions
            if generated_parts % 100 == 0 or generated_parts == config.partitions:
                self._report_progress(
                    f"Generated {generated_parts}/{config.partitions} partitions",
                    percent * 0.95,  # Reserve 5% for metadata
                )

        # Write metadata
        metadata = gen.create_metadata()
        with fs.open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        self._report_progress("Dataset generation complete", 100)

        return {
            "status": "generated",
            "message": f"Generated {config.partitions} partitions",
            "dataset_hash": gen.dataset_hash,
            "total_spans": config.total_spans,
            "path": config.s3_path,
        }

    async def verify_dataset(self) -> dict:
        """Verify dataset exists and is valid.

        Returns:
            Dictionary with verification results
        """
        import s3fs

        config = self.config
        gen = self._dataset_generator

        s3_options = {}
        if config.s3_endpoint:
            s3_options["client_kwargs"] = {"endpoint_url": config.s3_endpoint}
        if config.aws_access_key_id:
            s3_options["key"] = config.aws_access_key_id
            s3_options["secret"] = config.aws_secret_access_key
        elif config.aws_profile:
            s3_options["profile"] = config.aws_profile

        fs = s3fs.S3FileSystem(**s3_options) if s3_options else s3fs.S3FileSystem()

        # Check metadata
        metadata_path = f"{config.s3_bucket}/{config.s3_prefix}/_metadata.json"
        if not fs.exists(metadata_path):
            return {
                "valid": False,
                "error": "Metadata file not found",
                "path": config.s3_path,
            }

        with fs.open(metadata_path, 'r') as f:
            metadata = json.load(f)

        # Verify hash
        if metadata.get("dataset_hash") != gen.dataset_hash:
            return {
                "valid": False,
                "error": "Dataset hash mismatch",
                "expected": gen.dataset_hash,
                "found": metadata.get("dataset_hash"),
            }

        # Count partitions
        prefix = f"{config.s3_bucket}/{config.s3_prefix}/"
        files = fs.glob(f"{prefix}part-*.parquet")

        if len(files) != config.partitions:
            return {
                "valid": False,
                "error": f"Expected {config.partitions} partitions, found {len(files)}",
            }

        return {
            "valid": True,
            "dataset_hash": gen.dataset_hash,
            "partitions": len(files),
            "total_spans": metadata.get("total_spans"),
            "path": config.s3_path,
        }

    async def run_benchmarks(
        self,
        workers: Optional[list[int]] = None,
        zooms: Optional[list[str]] = None,
        runs: Optional[int] = None,
    ) -> BenchmarkRun:
        """Run full benchmark suite.

        Args:
            workers: Worker counts to test (default: from config)
            zooms: Zoom levels to test (default: from config)
            runs: Runs per config (default: from config)

        Returns:
            BenchmarkRun with all results
        """
        config = self.config

        worker_counts = workers or config.worker_counts
        zoom_levels = zooms or [name for name, _ in config.zoom_levels]
        zoom_map = {name: frac for name, frac in config.zoom_levels}
        runs_per_config = runs or config.runs_per_config
        warmup_runs = config.warmup_runs

        # Create run record
        run_name = f"benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run = BenchmarkRun(
            config_hash=compute_dataset_hash(config),
            run_name=run_name,
            metadata={
                "config": config.to_dict(),
                "environment": EnvironmentMetadata.gather().to_dict(),
            }
        )

        # Initialize datashader benchmark
        self._ds_benchmark = DatashaderBenchmark(config)

        self._report_progress("Loading dataset", 0)
        self._ds_benchmark.load_data()

        self._report_progress("Computing data bounds", 2)
        self._ds_benchmark.compute_bounds()

        # Calculate total iterations for progress
        total_runs_per_worker = len(zoom_levels) * (runs_per_config + warmup_runs)
        total_iterations = len(worker_counts) * total_runs_per_worker
        current_iteration = 0

        run_id = 0
        for worker_count in worker_counts:
            self._report_progress(
                f"Scaling to {worker_count} workers",
                5 + 90 * current_iteration / total_iterations,
            )

            # Scale cluster
            success = self._cluster_manager.scale_to(worker_count)
            if not success:
                logger.warning(
                    f"Could not scale to {worker_count} workers, "
                    "running with available workers"
                )

            cluster_state = self._cluster_manager.get_state()
            actual_workers = cluster_state.worker_count

            for zoom_name in zoom_levels:
                zoom_fraction = zoom_map.get(zoom_name, 1.0)

                # Warmup runs
                for warmup_idx in range(warmup_runs):
                    self._report_progress(
                        f"Warmup {warmup_idx + 1}/{warmup_runs} "
                        f"[{actual_workers}w, {zoom_name}]",
                        5 + 90 * current_iteration / total_iterations,
                    )

                    metrics = self._ds_benchmark.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=True,
                    )
                    run.add_metric(metrics)
                    run_id += 1
                    current_iteration += 1

                # Production runs
                for run_idx in range(runs_per_config):
                    self._report_progress(
                        f"Run {run_idx + 1}/{runs_per_config} "
                        f"[{actual_workers}w, {zoom_name}]",
                        5 + 90 * current_iteration / total_iterations,
                    )

                    metrics = self._ds_benchmark.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=False,
                    )

                    # Add cluster metrics
                    state = self._cluster_manager.get_state()
                    metrics.memory_per_worker = state.memory_per_worker
                    metrics.peak_memory_pct = state.memory_used_pct
                    metrics.spill_count = state.spill_count
                    metrics.worker_transfer_bytes = state.transfer_bytes

                    run.add_metric(metrics)
                    run_id += 1
                    current_iteration += 1

        run.mark_complete()
        self._report_progress("Benchmark complete", 100)

        # Save results
        output_dir = config.get_run_output_dir(run_name)
        output_dir.mkdir(parents=True, exist_ok=True)

        run.save(output_dir / "run.json")
        config.save(output_dir / "config.toml")

        return run

    async def run_benchmarks_streaming(
        self,
        workers: Optional[list[int]] = None,
        zooms: Optional[list[str]] = None,
        runs: Optional[int] = None,
    ) -> AsyncIterator[BenchmarkMetrics]:
        """Run benchmarks, yielding metrics as they complete.

        This allows real-time progress monitoring and early termination.

        Args:
            workers: Worker counts to test
            zooms: Zoom levels to test
            runs: Runs per config

        Yields:
            BenchmarkMetrics as each iteration completes
        """
        config = self.config

        worker_counts = workers or config.worker_counts
        zoom_levels = zooms or [name for name, _ in config.zoom_levels]
        zoom_map = {name: frac for name, frac in config.zoom_levels}
        runs_per_config = runs or config.runs_per_config
        warmup_runs = config.warmup_runs

        # Initialize
        self._ds_benchmark = DatashaderBenchmark(config)
        self._ds_benchmark.load_data()
        self._ds_benchmark.compute_bounds()

        run_id = 0
        for worker_count in worker_counts:
            self._cluster_manager.scale_to(worker_count)
            state = self._cluster_manager.get_state()
            actual_workers = state.worker_count

            for zoom_name in zoom_levels:
                zoom_fraction = zoom_map.get(zoom_name, 1.0)

                # Warmup runs
                for _ in range(warmup_runs):
                    metrics = self._ds_benchmark.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=True,
                    )
                    yield metrics
                    run_id += 1

                # Production runs
                for _ in range(runs_per_config):
                    metrics = self._ds_benchmark.run_iteration(
                        zoom_name=zoom_name,
                        zoom_fraction=zoom_fraction,
                        run_id=run_id,
                        worker_count=actual_workers,
                        is_warmup=False,
                    )

                    state = self._cluster_manager.get_state()
                    metrics.memory_per_worker = state.memory_per_worker
                    metrics.peak_memory_pct = state.memory_used_pct

                    yield metrics
                    run_id += 1


async def run_full_benchmark(
    config: BenchmarkConfig,
    generate: bool = True,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> BenchmarkRun:
    """Convenience function to run complete benchmark.

    Args:
        config: Benchmark configuration
        generate: Whether to generate dataset if needed
        progress_callback: Optional progress callback

    Returns:
        BenchmarkRun with results
    """
    executor = BenchmarkExecutor(config, progress_callback)

    if generate:
        await executor.generate_dataset()

    return await executor.run_benchmarks()
