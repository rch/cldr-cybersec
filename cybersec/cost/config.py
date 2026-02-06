"""Cost tracking configuration and data models.

This module defines the configuration dataclass and data models for
cost tracking including estimates, actuals, and delta calculations.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class CostSource(Enum):
    """Source of cost data."""
    COST_EXPLORER = "cost_explorer"     # Actual from AWS CE
    RESOURCE_INVENTORY = "inventory"     # Estimated from resources
    MANUAL = "manual"                    # User-provided


@dataclass
class ResourceCost:
    """Cost for a single resource type.

    Attributes:
        service: AWS service (ec2, s3, nat, elb, etc.)
        resource_type: Specific type (m6i.xlarge, gp3, etc.)
        quantity: Number of resources
        unit_cost_hourly: Hourly cost per unit in USD
        hours: Hours of usage
        total_cost: Computed total cost in USD
        region: AWS region
        resource_id: Optional resource identifier
    """
    service: str
    resource_type: str
    quantity: int
    unit_cost_hourly: float
    hours: float
    total_cost: float
    region: str = "us-east-1"
    resource_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "service": self.service,
            "resource_type": self.resource_type,
            "quantity": self.quantity,
            "unit_cost_hourly": self.unit_cost_hourly,
            "hours": self.hours,
            "total_cost": self.total_cost,
            "region": self.region,
            "resource_id": self.resource_id,
        }


@dataclass
class CostEstimate:
    """Single cost estimate with source tracking.

    Attributes:
        timestamp: When this estimate was created
        period_start: Start of cost period
        period_end: End of cost period
        source: Where the data came from
        total_cost: Total cost in USD
        by_service: Cost breakdown by service
        resources: Detailed resource costs
        region: AWS region
        account_id: AWS account ID
        confidence: Estimate confidence (high, medium, low)
        notes: Additional notes
    """
    timestamp: datetime
    period_start: datetime
    period_end: datetime
    source: CostSource
    total_cost: float
    by_service: dict[str, float] = field(default_factory=dict)
    resources: list[ResourceCost] = field(default_factory=list)
    region: str = "us-east-1"
    account_id: str = ""
    confidence: str = "medium"
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "source": self.source.value,
            "total_cost": self.total_cost,
            "by_service": self.by_service,
            "resources": [r.to_dict() for r in self.resources],
            "region": self.region,
            "account_id": self.account_id,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class CostDelta:
    """Track difference between actual and estimated costs.

    This serves as a loss function for improving estimation accuracy.

    Attributes:
        timestamp: When this comparison was made
        period: Cost period (e.g., "2026-02")
        actual: Actual cost from Cost Explorer (None if unavailable)
        estimated: Our calculated estimate
        delta: actual - estimated
        percentage_error: |delta| / actual * 100
        by_service: Per-service deltas
    """
    timestamp: datetime
    period: str
    actual: Optional[float]
    estimated: float
    delta: float
    percentage_error: float
    by_service: dict[str, float] = field(default_factory=dict)

    @property
    def absolute_loss(self) -> float:
        """Absolute error (L1 loss)."""
        return abs(self.delta)

    @property
    def squared_loss(self) -> float:
        """Squared error (L2 loss)."""
        return self.delta ** 2

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "period": self.period,
            "actual": self.actual,
            "estimated": self.estimated,
            "delta": self.delta,
            "percentage_error": self.percentage_error,
            "by_service": self.by_service,
            "absolute_loss": self.absolute_loss,
            "squared_loss": self.squared_loss,
        }


@dataclass
class CostConfig:
    """Cost tracking configuration.

    Attributes:
        enabled: Whether cost tracking is enabled
        aws_region: Primary AWS region
        aws_profile: AWS credentials profile name
        cache_dir: Directory for caching cost data
        cache_ttl_hours: Cache time-to-live
        poll_interval_seconds: How often to poll AWS
        metrics_port: Port for Prometheus metrics endpoint
        use_cost_explorer: Whether to try Cost Explorer first
        fallback_to_inventory: Estimate from resources if CE fails
        monthly_budget: Budget for alerting
        alert_threshold: Alert at this percentage of budget
    """
    enabled: bool = True
    aws_region: str = "us-east-1"
    aws_profile: str = "default"
    cache_dir: str = ".cybersec/cost-cache"
    cache_ttl_hours: int = 24
    poll_interval_seconds: int = 300  # 5 minutes
    metrics_port: int = 9876
    use_cost_explorer: bool = True
    fallback_to_inventory: bool = True
    monthly_budget: float = 500.0
    alert_threshold: float = 0.8  # Alert at 80%

    @classmethod
    def from_env(cls) -> "CostConfig":
        """Create configuration from environment variables."""
        return cls(
            enabled=os.environ.get("COST_ENABLED", "true").lower() == "true",
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            aws_profile=os.environ.get("AWS_PROFILE", "default"),
            cache_dir=os.environ.get("COST_CACHE_DIR", ".cybersec/cost-cache"),
            poll_interval_seconds=int(os.environ.get("COST_POLL_INTERVAL", "300")),
            metrics_port=int(os.environ.get("COST_METRICS_PORT", "9876")),
            monthly_budget=float(os.environ.get("COST_MONTHLY_BUDGET", "500.0")),
        )

    def get_cache_path(self) -> Path:
        """Get cache directory path, creating if needed."""
        path = Path(self.cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "enabled": self.enabled,
            "aws_region": self.aws_region,
            "aws_profile": self.aws_profile,
            "cache_dir": self.cache_dir,
            "cache_ttl_hours": self.cache_ttl_hours,
            "poll_interval_seconds": self.poll_interval_seconds,
            "metrics_port": self.metrics_port,
            "use_cost_explorer": self.use_cost_explorer,
            "fallback_to_inventory": self.fallback_to_inventory,
            "monthly_budget": self.monthly_budget,
            "alert_threshold": self.alert_threshold,
        }
