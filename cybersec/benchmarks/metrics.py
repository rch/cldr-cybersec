"""Benchmark metrics for Dask/Datashader scaling study.

This module defines dataclasses for capturing comprehensive benchmark metrics
including timing, throughput, memory, Dask internals, and S3 I/O.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import json


@dataclass
class BenchmarkMetrics:
    """Metrics captured for a single benchmark run.

    Identity:
        run_id: Sequential run identifier
        worker_count: Number of Dask workers
        zoom_level: Zoom level name (e.g., "full", "10pct")
        zoom_fraction: Zoom level as fraction (e.g., 1.0, 0.1)
        is_warmup: Whether this run is a warmup (discarded from stats)

    Timing (nanoseconds):
        wall_clock_ns: Total wall clock time
        aggregation_ns: Time spent in datashader aggregation
        shade_ns: Time spent in datashader shading

    Throughput:
        points_processed: Number of data points processed
        points_per_second: Processing rate
        gb_processed: Data volume processed in GB
        gb_per_second: Data throughput in GB/s

    Memory:
        memory_per_worker: Memory usage per worker in bytes
        peak_memory_pct: Peak memory as percentage of available
        spill_count: Number of Dask spill events

    Dask Internals:
        task_graph_size: Number of tasks in graph
        worker_transfer_bytes: Bytes transferred between workers
        task_duration_mean_ms: Mean task duration in milliseconds
        task_duration_std_ms: Std dev of task duration

    S3 I/O:
        s3_read_bytes: Total bytes read from S3
        s3_read_ops: Number of S3 read operations
        s3_throughput_mbps: S3 read throughput in MB/s
    """

    # Identity
    run_id: int
    worker_count: int
    zoom_level: str
    zoom_fraction: float
    is_warmup: bool = False
    timestamp: datetime = field(default_factory=datetime.now)

    # Timing (nanoseconds for precision)
    wall_clock_ns: int = 0
    aggregation_ns: int = 0
    shade_ns: int = 0

    # Throughput
    points_processed: int = 0
    points_per_second: float = 0.0
    gb_processed: float = 0.0
    gb_per_second: float = 0.0

    # Memory
    memory_per_worker: list[int] = field(default_factory=list)
    peak_memory_pct: float = 0.0
    spill_count: int = 0

    # Dask internals
    task_graph_size: int = 0
    worker_transfer_bytes: int = 0
    task_duration_mean_ms: float = 0.0
    task_duration_std_ms: float = 0.0

    # S3 I/O
    s3_read_bytes: int = 0
    s3_read_ops: int = 0
    s3_throughput_mbps: float = 0.0

    # Error tracking
    error: Optional[str] = None

    @property
    def wall_clock_seconds(self) -> float:
        """Wall clock time in seconds."""
        return self.wall_clock_ns / 1e9

    @property
    def aggregation_seconds(self) -> float:
        """Aggregation time in seconds."""
        return self.aggregation_ns / 1e9

    @property
    def shade_seconds(self) -> float:
        """Shade time in seconds."""
        return self.shade_ns / 1e9

    @property
    def total_memory_bytes(self) -> int:
        """Total memory across all workers."""
        return sum(self.memory_per_worker)

    @property
    def mean_memory_bytes(self) -> float:
        """Mean memory per worker."""
        if not self.memory_per_worker:
            return 0.0
        return sum(self.memory_per_worker) / len(self.memory_per_worker)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "run_id": self.run_id,
            "worker_count": self.worker_count,
            "zoom_level": self.zoom_level,
            "zoom_fraction": self.zoom_fraction,
            "is_warmup": self.is_warmup,
            "timestamp": self.timestamp.isoformat(),
            "wall_clock_ns": self.wall_clock_ns,
            "aggregation_ns": self.aggregation_ns,
            "shade_ns": self.shade_ns,
            "points_processed": self.points_processed,
            "points_per_second": self.points_per_second,
            "gb_processed": self.gb_processed,
            "gb_per_second": self.gb_per_second,
            "memory_per_worker": self.memory_per_worker,
            "peak_memory_pct": self.peak_memory_pct,
            "spill_count": self.spill_count,
            "task_graph_size": self.task_graph_size,
            "worker_transfer_bytes": self.worker_transfer_bytes,
            "task_duration_mean_ms": self.task_duration_mean_ms,
            "task_duration_std_ms": self.task_duration_std_ms,
            "s3_read_bytes": self.s3_read_bytes,
            "s3_read_ops": self.s3_read_ops,
            "s3_throughput_mbps": self.s3_throughput_mbps,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkMetrics":
        """Create from dictionary."""
        data = data.copy()
        if "timestamp" in data and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass
class BenchmarkRun:
    """Container for a complete benchmark run with all metrics.

    Attributes:
        config_hash: Hash of configuration for reproducibility
        run_name: Human-readable run name
        started_at: Start timestamp
        completed_at: Completion timestamp
        metrics: List of all BenchmarkMetrics (including warmup)
        metadata: Environment and system metadata
    """

    config_hash: str
    run_name: str
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    metrics: list[BenchmarkMetrics] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def production_metrics(self) -> list[BenchmarkMetrics]:
        """Get only non-warmup metrics."""
        return [m for m in self.metrics if not m.is_warmup]

    @property
    def warmup_metrics(self) -> list[BenchmarkMetrics]:
        """Get only warmup metrics."""
        return [m for m in self.metrics if m.is_warmup]

    @property
    def duration_seconds(self) -> Optional[float]:
        """Total run duration in seconds."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def add_metric(self, metric: BenchmarkMetrics) -> None:
        """Add a metric to the run."""
        self.metrics.append(metric)

    def mark_complete(self) -> None:
        """Mark the run as complete."""
        self.completed_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "config_hash": self.config_hash,
            "run_name": self.run_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metrics": [m.to_dict() for m in self.metrics],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkRun":
        """Create from dictionary."""
        data = data.copy()
        if "started_at" in data and isinstance(data["started_at"], str):
            data["started_at"] = datetime.fromisoformat(data["started_at"])
        if "completed_at" in data and isinstance(data["completed_at"], str):
            data["completed_at"] = datetime.fromisoformat(data["completed_at"])
        if "metrics" in data:
            data["metrics"] = [
                BenchmarkMetrics.from_dict(m) for m in data["metrics"]
            ]
        return cls(**data)

    def save(self, path: Path) -> None:
        """Save run to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "BenchmarkRun":
        """Load run from JSON file."""
        with open(path) as f:
            return cls.from_dict(json.load(f))


@dataclass
class ScalingResult:
    """Computed scaling metrics from benchmark runs.

    Attributes:
        worker_count: Number of workers
        zoom_level: Zoom level name
        mean_time_s: Mean wall clock time in seconds
        std_time_s: Standard deviation of time
        speedup: Speedup relative to baseline (T_1 / T_n)
        efficiency: Parallel efficiency (Speedup / n)
        throughput_pts_s: Mean throughput in points/second
        throughput_gb_s: Mean throughput in GB/s
        amdahls_fraction: Estimated serial fraction (derived from Amdahl's law)
    """

    worker_count: int
    zoom_level: str
    mean_time_s: float
    std_time_s: float
    speedup: float
    efficiency: float
    throughput_pts_s: float
    throughput_gb_s: float
    amdahls_fraction: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "worker_count": self.worker_count,
            "zoom_level": self.zoom_level,
            "mean_time_s": self.mean_time_s,
            "std_time_s": self.std_time_s,
            "speedup": self.speedup,
            "efficiency": self.efficiency,
            "throughput_pts_s": self.throughput_pts_s,
            "throughput_gb_s": self.throughput_gb_s,
            "amdahls_fraction": self.amdahls_fraction,
        }


@dataclass
class EnvironmentMetadata:
    """System and environment metadata for reproducibility.

    Captures hardware, software versions, and configuration details.
    """

    # Hardware
    cpu_model: str = ""
    cpu_cores: int = 0
    memory_total_gb: float = 0.0
    gpu_models: list[str] = field(default_factory=list)

    # Software versions
    python_version: str = ""
    dask_version: str = ""
    datashader_version: str = ""
    pandas_version: str = ""
    numpy_version: str = ""
    pyarrow_version: str = ""

    # Cluster configuration
    dask_scheduler_address: str = ""
    kubernetes_version: str = ""
    node_count: int = 0

    # S3 configuration
    s3_endpoint: str = ""
    s3_region: str = ""

    @classmethod
    def gather(cls) -> "EnvironmentMetadata":
        """Gather current environment metadata."""
        import platform
        import sys

        meta = cls()

        # Hardware
        meta.cpu_model = platform.processor() or "unknown"
        try:
            import os
            meta.cpu_cores = os.cpu_count() or 0
        except Exception:
            pass

        try:
            import psutil
            meta.memory_total_gb = psutil.virtual_memory().total / (1024**3)
        except ImportError:
            pass

        # Software versions
        meta.python_version = sys.version.split()[0]

        try:
            import dask
            meta.dask_version = dask.__version__
        except ImportError:
            meta.dask_version = "not installed"

        try:
            import datashader
            meta.datashader_version = datashader.__version__
        except ImportError:
            meta.datashader_version = "not installed"

        try:
            import pandas
            meta.pandas_version = pandas.__version__
        except ImportError:
            meta.pandas_version = "not installed"

        try:
            import numpy
            meta.numpy_version = numpy.__version__
        except ImportError:
            meta.numpy_version = "not installed"

        try:
            import pyarrow
            meta.pyarrow_version = pyarrow.__version__
        except ImportError:
            meta.pyarrow_version = "not installed"

        return meta

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "cpu_model": self.cpu_model,
            "cpu_cores": self.cpu_cores,
            "memory_total_gb": self.memory_total_gb,
            "gpu_models": self.gpu_models,
            "python_version": self.python_version,
            "dask_version": self.dask_version,
            "datashader_version": self.datashader_version,
            "pandas_version": self.pandas_version,
            "numpy_version": self.numpy_version,
            "pyarrow_version": self.pyarrow_version,
            "dask_scheduler_address": self.dask_scheduler_address,
            "kubernetes_version": self.kubernetes_version,
            "node_count": self.node_count,
            "s3_endpoint": self.s3_endpoint,
            "s3_region": self.s3_region,
        }
