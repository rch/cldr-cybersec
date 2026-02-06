"""AWS Cost Observability Module.

Provides continuous cost monitoring integrated with the observability stack.
While devenv is active, tracks AWS spend via Prometheus metrics.

Features:
- Real-time MTD cost from AWS Cost Explorer
- Resource inventory-based estimation (fallback when CE unavailable)
- Delta tracking between actual and estimated costs (loss function)
- Per-service cost breakdown
- Prometheus metrics for dashboards and alerting

Usage:
    # Start cost monitor (as devenv process)
    uv run python -m cybersec.cost.monitor

    # Query current costs via CLI
    cybersec "/cost status"
    cybersec "/cost estimate"
    cybersec "/cost delta"
"""

from .config import (
    CostConfig,
    CostSource,
    ResourceCost,
    CostEstimate,
    CostDelta,
)
from .aws_client import AWSCostClient
from .estimator import ResourceEstimator
from .monitor import CostMonitor, run_monitor

__all__ = [
    # Config and data models
    "CostConfig",
    "CostSource",
    "ResourceCost",
    "CostEstimate",
    "CostDelta",
    # Client
    "AWSCostClient",
    # Estimator
    "ResourceEstimator",
    # Monitor
    "CostMonitor",
    "run_monitor",
]
