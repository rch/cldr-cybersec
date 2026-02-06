"""Dask cluster scaling helpers for AWS-based benchmarks.

This module provides utilities for scaling Dask clusters and
collecting cluster-level metrics during benchmarks.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from .config import BenchmarkConfig


@dataclass
class ClusterState:
    """State snapshot of a Dask cluster.

    Attributes:
        worker_count: Number of active workers
        scheduler_address: Scheduler address
        memory_total_bytes: Total memory across workers
        memory_used_bytes: Used memory across workers
        memory_per_worker: Memory per worker
        spill_count: Number of spill events
        transfer_bytes: Bytes transferred between workers
    """
    worker_count: int
    scheduler_address: str
    memory_total_bytes: int = 0
    memory_used_bytes: int = 0
    memory_per_worker: list[int] = None
    spill_count: int = 0
    transfer_bytes: int = 0

    def __post_init__(self):
        if self.memory_per_worker is None:
            self.memory_per_worker = []

    @property
    def memory_used_pct(self) -> float:
        """Memory usage as percentage."""
        if self.memory_total_bytes == 0:
            return 0.0
        return 100 * self.memory_used_bytes / self.memory_total_bytes


def get_dask_client(scheduler_address: str = None):
    """Get or create Dask distributed client.

    Args:
        scheduler_address: Dask scheduler address (tcp://host:port).
                          Required for production use. If not provided,
                          tries to get existing client.

    Returns:
        distributed.Client

    Raises:
        ValueError: If no scheduler address and no existing client
    """
    from distributed import Client
    import os

    # Check environment variable
    if scheduler_address is None:
        scheduler_address = os.environ.get("DASK_SCHEDULER")

    if scheduler_address:
        return Client(scheduler_address)

    try:
        # Try to get existing client
        return Client.current()
    except ValueError:
        raise ValueError(
            "No Dask scheduler configured. Provide scheduler_address or set "
            "DASK_SCHEDULER environment variable (e.g., tcp://scheduler:8786). "
            "Local clusters are not supported - deploy Dask on your infrastructure."
        )


def scale_cluster(target_workers: int, timeout: float = 120.0) -> bool:
    """Scale Dask cluster to target worker count.

    Uses client.cluster.scale() for adaptive clusters, or waits
    for workers to be available for fixed clusters.

    Args:
        target_workers: Desired number of workers
        timeout: Maximum time to wait in seconds

    Returns:
        True if scaling succeeded
    """
    from distributed import Client

    client = get_dask_client()
    cluster = getattr(client, 'cluster', None)

    if cluster is not None and hasattr(cluster, 'scale'):
        # Adaptive cluster - use scale()
        cluster.scale(target_workers)
    else:
        # Fixed cluster or remote - just wait for workers
        pass

    # Wait for workers to be available
    start = time.time()
    while time.time() - start < timeout:
        n_workers = len(client.scheduler_info()['workers'])
        if n_workers >= target_workers:
            return True
        time.sleep(1)

    return False


async def scale_cluster_async(target_workers: int, timeout: float = 120.0) -> bool:
    """Async version of scale_cluster.

    Args:
        target_workers: Desired number of workers
        timeout: Maximum time to wait

    Returns:
        True if scaling succeeded
    """
    return await asyncio.get_event_loop().run_in_executor(
        None, scale_cluster, target_workers, timeout
    )


def get_cluster_state() -> ClusterState:
    """Get current state of Dask cluster.

    Returns:
        ClusterState with current metrics
    """
    client = get_dask_client()
    info = client.scheduler_info()

    workers = info.get('workers', {})
    n_workers = len(workers)

    # Collect memory per worker
    memory_per_worker = []
    total_memory = 0
    used_memory = 0

    for worker_info in workers.values():
        # Memory info varies by Dask version
        if 'memory_limit' in worker_info:
            total_memory += worker_info['memory_limit']
        if 'memory' in worker_info:
            used = worker_info['memory']
            used_memory += used
            memory_per_worker.append(used)

    return ClusterState(
        worker_count=n_workers,
        scheduler_address=info.get('address', 'unknown'),
        memory_total_bytes=total_memory,
        memory_used_bytes=used_memory,
        memory_per_worker=memory_per_worker,
    )


def get_worker_metrics() -> dict[str, Any]:
    """Get detailed metrics from all workers.

    Returns:
        Dictionary with worker metrics
    """
    client = get_dask_client()

    try:
        # Get metrics from scheduler
        info = client.scheduler_info()

        metrics = {
            "workers": {},
            "total": {
                "memory_used": 0,
                "memory_limit": 0,
                "cpu_count": 0,
                "tasks_completed": 0,
            }
        }

        for worker_addr, worker_info in info.get('workers', {}).items():
            worker_metrics = {
                "memory_used": worker_info.get('memory', 0),
                "memory_limit": worker_info.get('memory_limit', 0),
                "cpu_count": worker_info.get('nthreads', 0),
            }
            metrics["workers"][worker_addr] = worker_metrics

            metrics["total"]["memory_used"] += worker_metrics["memory_used"]
            metrics["total"]["memory_limit"] += worker_metrics["memory_limit"]
            metrics["total"]["cpu_count"] += worker_metrics["cpu_count"]

        return metrics

    except Exception as e:
        return {"error": str(e)}


def get_transfer_metrics() -> dict[str, int]:
    """Get data transfer metrics between workers.

    Returns:
        Dictionary with transfer statistics
    """
    client = get_dask_client()

    try:
        # This requires access to worker metrics
        # which may not be available in all configurations
        info = client.scheduler_info()

        total_transfer = 0
        for worker_info in info.get('workers', {}).values():
            # Transfer metrics vary by Dask version
            if 'metrics' in worker_info:
                metrics = worker_info['metrics']
                total_transfer += metrics.get('transfer_outgoing_bytes', 0)

        return {
            "total_bytes": total_transfer,
        }

    except Exception as e:
        return {"error": str(e), "total_bytes": 0}


class ClusterManager:
    """Manager for Dask cluster operations during benchmarks.

    Handles cluster scaling, metric collection, and state management.
    Requires an external Dask scheduler - does not create local clusters.
    """

    def __init__(self, config: BenchmarkConfig, scheduler_address: str = None):
        """Initialize cluster manager.

        Args:
            config: Benchmark configuration
            scheduler_address: Dask scheduler address (tcp://host:port).
                              Can also be set via DASK_SCHEDULER env var.
        """
        self.config = config
        self._scheduler_address = scheduler_address
        self._client = None
        self._initial_state = None

    @property
    def client(self):
        """Get Dask client, connecting to scheduler if necessary."""
        if self._client is None:
            self._client = get_dask_client(self._scheduler_address)
        return self._client

    def scale_to(self, n_workers: int, timeout: float = 120.0) -> bool:
        """Scale cluster to specified worker count.

        Args:
            n_workers: Target worker count
            timeout: Maximum wait time

        Returns:
            True if successful
        """
        return scale_cluster(n_workers, timeout)

    def get_state(self) -> ClusterState:
        """Get current cluster state.

        Returns:
            ClusterState snapshot
        """
        return get_cluster_state()

    def record_initial_state(self) -> ClusterState:
        """Record initial state for delta calculations.

        Returns:
            Initial ClusterState
        """
        self._initial_state = self.get_state()
        return self._initial_state

    def get_state_delta(self) -> dict[str, Any]:
        """Get change in state since initial recording.

        Returns:
            Dictionary with state changes
        """
        if self._initial_state is None:
            return {}

        current = self.get_state()

        return {
            "memory_delta_bytes": (
                current.memory_used_bytes - self._initial_state.memory_used_bytes
            ),
            "transfer_delta_bytes": (
                current.transfer_bytes - self._initial_state.transfer_bytes
            ),
            "spill_delta": current.spill_count - self._initial_state.spill_count,
        }

    def wait_for_workers(
        self,
        min_workers: int,
        timeout: float = 120.0
    ) -> bool:
        """Wait for minimum number of workers to be available.

        Args:
            min_workers: Minimum required workers
            timeout: Maximum wait time

        Returns:
            True if sufficient workers available
        """
        start = time.time()
        while time.time() - start < timeout:
            state = self.get_state()
            if state.worker_count >= min_workers:
                return True
            time.sleep(1)
        return False

    def verify_cluster_ready(self) -> tuple[bool, str]:
        """Verify cluster is ready for benchmarking.

        Returns:
            Tuple of (is_ready, message)
        """
        try:
            state = self.get_state()

            if state.worker_count == 0:
                return False, "No workers available"

            # Check that workers have memory
            if state.memory_total_bytes == 0:
                return False, "Workers have no memory reported"

            # Check that scheduler is reachable
            if not state.scheduler_address or state.scheduler_address == 'unknown':
                return False, "Cannot determine scheduler address"

            return True, f"Cluster ready with {state.worker_count} workers"

        except Exception as e:
            return False, f"Error checking cluster: {e}"
