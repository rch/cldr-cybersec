"""Dask/Datashader Scaling Benchmark Framework.

A fully automated, reproducible benchmarking framework for characterizing
Dask/Datashader scaling with out-of-core processing.

Features:
- Fixed dataset with seeded RNG for exact reproduction
- Multi-zoom scaling matrix across worker counts
- True out-of-core datashader processing (no .compute() before rendering)
- Comprehensive metrics (timing, throughput, memory, Dask internals)
- Publication-quality figures with matplotlib

IMPORTANT: This framework requires an external Dask scheduler running on
AWS infrastructure. Local clusters are not supported. Set DASK_SCHEDULER
environment variable or use --scheduler option.
"""

from .config import BenchmarkConfig, BENCHMARK_FULL, BENCHMARK_QUICK
from .metrics import BenchmarkMetrics, BenchmarkRun, ScalingResult, EnvironmentMetadata
from .dataset import DatasetGenerator, generate_spans_seeded, compute_dataset_hash
from .analysis import analyze_run, compute_scaling_results
from .executor import BenchmarkExecutor, run_full_benchmark

__all__ = [
    # Config
    "BenchmarkConfig",
    "BENCHMARK_FULL",
    "BENCHMARK_QUICK",
    # Metrics
    "BenchmarkMetrics",
    "BenchmarkRun",
    "ScalingResult",
    "EnvironmentMetadata",
    # Dataset
    "DatasetGenerator",
    "generate_spans_seeded",
    "compute_dataset_hash",
    # Analysis
    "analyze_run",
    "compute_scaling_results",
    # Executor
    "BenchmarkExecutor",
    "run_full_benchmark",
]
