"""Benchmark configuration for Dask/Datashader scaling studies.

This module defines the configuration dataclass for reproducible benchmarking.
All parameters have defaults tuned for the 120GB benchmark dataset.

IMPORTANT: Benchmarks run on AWS infrastructure with external Dask scheduler.
Local execution is not supported.

Environment Variables:
    BENCHMARK_S3_BUCKET: S3 bucket for benchmark data (default: from your AWS account)
    BENCHMARK_S3_PREFIX: S3 prefix within bucket (default: otel/benchmark-v1-120gb)
    AWS_PROFILE: AWS credentials profile (default: default)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import os
import tomllib

# Default bucket name - users should set BENCHMARK_S3_BUCKET for their own AWS account
_DEFAULT_S3_BUCKET = os.environ.get("BENCHMARK_S3_BUCKET", "cybersec-benchmark-data")


@dataclass
class BenchmarkConfig:
    """Configuration for Dask/Datashader scaling benchmark runs.

    Dataset Configuration:
        version: Dataset version identifier (e.g., "v1")
        total_spans: Total number of spans to generate (240M for 120GB)
        partitions: Number of Parquet partitions (2400 for ~100K spans each)
        random_seed: Fixed seed for reproducibility (42)
        s3_bucket: S3 bucket name (AWS)
        s3_prefix: Path prefix within bucket

    Scaling Matrix:
        worker_counts: List of Dask worker counts to test
        zoom_levels: List of (name, fraction) tuples for zoom levels

    Runs:
        runs_per_config: Number of timed runs per configuration
        warmup_runs: Runs to discard for warmup

    Canvas:
        canvas_width: Datashader canvas width in pixels
        canvas_height: Datashader canvas height in pixels

    Output:
        output_dir: Directory for benchmark results

    AWS Configuration:
        s3_endpoint: None for AWS S3 (required for production)
        aws_profile: AWS credentials profile
    """

    # Dataset configuration
    version: str = "v1"
    total_spans: int = 240_000_000  # 240M spans = 120GB expanded
    partitions: int = 2400  # 100K spans per partition
    random_seed: int = 42  # Fixed for reproducibility
    s3_bucket: str = field(default_factory=lambda: _DEFAULT_S3_BUCKET)
    s3_prefix: str = field(default_factory=lambda: os.environ.get("BENCHMARK_S3_PREFIX", "otel/benchmark-v1-120gb"))

    # Scaling matrix
    worker_counts: list[int] = field(
        default_factory=lambda: [2, 4, 8, 16, 32]
    )
    zoom_levels: list[tuple[str, float]] = field(
        default_factory=lambda: [
            ("full", 1.0),      # 100% of data
            ("10pct", 0.1),     # 10% zoom
            ("1pct", 0.01),     # 1% zoom
            ("0.1pct", 0.001),  # 0.1% zoom
        ]
    )

    # Run configuration
    runs_per_config: int = 10
    warmup_runs: int = 2  # Discarded from statistics

    # Canvas configuration
    canvas_width: int = 1920
    canvas_height: int = 1080

    # Output configuration
    output_dir: Path = field(default_factory=lambda: Path("build/benchmarks"))

    # AWS S3 configuration (required for production)
    s3_endpoint: Optional[str] = None  # None = AWS S3 (production)
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_profile: Optional[str] = "default"  # AWS profile for credentials

    @property
    def s3_path(self) -> str:
        """Full S3 path to dataset."""
        return f"s3://{self.s3_bucket}/{self.s3_prefix}/"

    @property
    def spans_per_partition(self) -> int:
        """Number of spans per partition."""
        return self.total_spans // self.partitions

    @property
    def total_runs(self) -> int:
        """Total number of benchmark runs (including warmup)."""
        num_workers = len(self.worker_counts)
        num_zooms = len(self.zoom_levels)
        runs_with_warmup = self.runs_per_config + self.warmup_runs
        return num_workers * num_zooms * runs_with_warmup

    @property
    def estimated_size_gb(self) -> float:
        """Estimated expanded dataset size in GB.

        Assumes ~500 bytes per span (expanded, with all columns).
        """
        return (self.total_spans * 500) / (1024**3)

    @property
    def estimated_disk_size_gb(self) -> float:
        """Estimated on-disk size with Snappy compression (~5:1 ratio)."""
        return self.estimated_size_gb / 5

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "version": self.version,
            "total_spans": self.total_spans,
            "partitions": self.partitions,
            "random_seed": self.random_seed,
            "s3_bucket": self.s3_bucket,
            "s3_prefix": self.s3_prefix,
            "worker_counts": self.worker_counts,
            "zoom_levels": [(name, frac) for name, frac in self.zoom_levels],
            "runs_per_config": self.runs_per_config,
            "warmup_runs": self.warmup_runs,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "output_dir": str(self.output_dir),
            "s3_endpoint": self.s3_endpoint,
        }

    def save(self, path: Path) -> None:
        """Save configuration to TOML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        lines = []
        lines.append("# Dask/Datashader Benchmark Configuration")
        lines.append(f'version = "{data["version"]}"')
        lines.append(f'total_spans = {data["total_spans"]}')
        lines.append(f'partitions = {data["partitions"]}')
        lines.append(f'random_seed = {data["random_seed"]}')
        lines.append(f's3_bucket = "{data["s3_bucket"]}"')
        lines.append(f's3_prefix = "{data["s3_prefix"]}"')
        lines.append(f'worker_counts = {json.dumps(data["worker_counts"])}')
        lines.append(f'zoom_levels = {json.dumps(data["zoom_levels"])}')
        lines.append(f'runs_per_config = {data["runs_per_config"]}')
        lines.append(f'warmup_runs = {data["warmup_runs"]}')
        lines.append(f'canvas_width = {data["canvas_width"]}')
        lines.append(f'canvas_height = {data["canvas_height"]}')
        lines.append(f'output_dir = "{data["output_dir"]}"')
        if data.get("s3_endpoint"):
            lines.append(f's3_endpoint = "{data["s3_endpoint"]}"')
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    @classmethod
    def load(cls, path: Path) -> "BenchmarkConfig":
        """Load configuration from TOML file."""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        # Convert zoom_levels back to tuples
        if "zoom_levels" in data:
            data["zoom_levels"] = [
                (name, frac) for name, frac in data["zoom_levels"]
            ]

        # Convert output_dir to Path
        if "output_dir" in data:
            data["output_dir"] = Path(data["output_dir"])

        return cls(**data)

    def get_run_output_dir(self, run_name: str) -> Path:
        """Get output directory for a specific run."""
        return self.output_dir / run_name

    def validate(self) -> list[str]:
        """Validate configuration, returning list of errors."""
        errors = []

        if self.total_spans <= 0:
            errors.append("total_spans must be positive")

        if self.partitions <= 0:
            errors.append("partitions must be positive")

        if self.total_spans % self.partitions != 0:
            errors.append(
                f"total_spans ({self.total_spans}) must be divisible by "
                f"partitions ({self.partitions})"
            )

        if not self.worker_counts:
            errors.append("worker_counts must not be empty")

        if any(w <= 0 for w in self.worker_counts):
            errors.append("all worker_counts must be positive")

        if not self.zoom_levels:
            errors.append("zoom_levels must not be empty")

        for name, frac in self.zoom_levels:
            if not (0 < frac <= 1.0):
                errors.append(f"zoom level '{name}' fraction must be in (0, 1]")

        if self.runs_per_config <= 0:
            errors.append("runs_per_config must be positive")

        if self.warmup_runs < 0:
            errors.append("warmup_runs must be non-negative")

        if self.canvas_width <= 0 or self.canvas_height <= 0:
            errors.append("canvas dimensions must be positive")

        return errors


# Pre-configured profiles for AWS deployment
# NOTE: All profiles require external Dask scheduler on AWS infrastructure

BENCHMARK_FULL = BenchmarkConfig()  # Full 120GB benchmark (production)

BENCHMARK_QUICK = BenchmarkConfig(
    total_spans=24_000_000,  # 24M = 12GB (validation runs)
    partitions=240,
    worker_counts=[2, 4, 8, 16],
    zoom_levels=[("full", 1.0), ("10pct", 0.1)],
    runs_per_config=3,
    warmup_runs=1,
    s3_prefix="otel/benchmark-quick-12gb",
)

# Backward compatibility aliases (deprecated)
SC26_FULL = BENCHMARK_FULL
SC26_QUICK = BENCHMARK_QUICK
