"""Tests for cost configuration module."""

import pytest
from datetime import datetime

from cybersec.cost.config import (
    CostConfig,
    CostSource,
    ResourceCost,
    CostEstimate,
    CostDelta,
)


class TestCostConfig:
    """Tests for CostConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CostConfig()
        assert config.enabled is True
        assert config.aws_region == "us-east-1"
        assert config.aws_profile == "default"
        assert config.poll_interval_seconds == 300
        assert config.metrics_port == 9876
        assert config.monthly_budget == 500.0
        assert config.alert_threshold == 0.8

    def test_to_dict(self):
        """Test configuration serialization."""
        config = CostConfig()
        data = config.to_dict()

        assert data["enabled"] is True
        assert data["aws_region"] == "us-east-1"
        assert data["poll_interval_seconds"] == 300
        assert "monthly_budget" in data


class TestCostSource:
    """Tests for CostSource enum."""

    def test_source_values(self):
        """Test source enum values."""
        assert CostSource.COST_EXPLORER.value == "cost_explorer"
        assert CostSource.RESOURCE_INVENTORY.value == "inventory"
        assert CostSource.MANUAL.value == "manual"


class TestResourceCost:
    """Tests for ResourceCost dataclass."""

    def test_basic_creation(self):
        """Test basic resource cost creation."""
        cost = ResourceCost(
            service="ec2",
            resource_type="m6i.xlarge",
            quantity=1,
            unit_cost_hourly=0.192,
            hours=100,
            total_cost=19.20,
        )

        assert cost.service == "ec2"
        assert cost.resource_type == "m6i.xlarge"
        assert cost.total_cost == 19.20
        assert cost.region == "us-east-1"  # default

    def test_to_dict(self):
        """Test resource cost serialization."""
        cost = ResourceCost(
            service="ebs",
            resource_type="gp3-100gb",
            quantity=1,
            unit_cost_hourly=0.01,
            hours=720,
            total_cost=7.20,
            resource_id="vol-123",
        )

        data = cost.to_dict()
        assert data["service"] == "ebs"
        assert data["resource_id"] == "vol-123"
        assert data["total_cost"] == 7.20


class TestCostEstimate:
    """Tests for CostEstimate dataclass."""

    def test_basic_creation(self):
        """Test basic estimate creation."""
        now = datetime.now()
        estimate = CostEstimate(
            timestamp=now,
            period_start=now.replace(day=1),
            period_end=now,
            source=CostSource.RESOURCE_INVENTORY,
            total_cost=150.0,
            by_service={"ec2": 100, "ebs": 50},
            account_id="123456789012",
        )

        assert estimate.total_cost == 150.0
        assert estimate.source == CostSource.RESOURCE_INVENTORY
        assert estimate.confidence == "medium"

    def test_to_dict(self):
        """Test estimate serialization."""
        now = datetime.now()
        estimate = CostEstimate(
            timestamp=now,
            period_start=now.replace(day=1),
            period_end=now,
            source=CostSource.COST_EXPLORER,
            total_cost=329.50,
            confidence="high",
        )

        data = estimate.to_dict()
        assert data["total_cost"] == 329.50
        assert data["source"] == "cost_explorer"
        assert data["confidence"] == "high"


class TestCostDelta:
    """Tests for CostDelta dataclass."""

    def test_basic_creation(self):
        """Test basic delta creation."""
        delta = CostDelta(
            timestamp=datetime.now(),
            period="2026-02",
            actual=329.50,
            estimated=45.00,
            delta=284.50,
            percentage_error=86.3,
        )

        assert delta.actual == 329.50
        assert delta.estimated == 45.00
        assert delta.delta == 284.50

    def test_loss_functions(self):
        """Test loss function properties."""
        delta = CostDelta(
            timestamp=datetime.now(),
            period="2026-02",
            actual=100.0,
            estimated=80.0,
            delta=20.0,
            percentage_error=20.0,
        )

        assert delta.absolute_loss == 20.0
        assert delta.squared_loss == 400.0

    def test_negative_delta(self):
        """Test negative delta (overestimate)."""
        delta = CostDelta(
            timestamp=datetime.now(),
            period="2026-02",
            actual=100.0,
            estimated=120.0,
            delta=-20.0,
            percentage_error=20.0,
        )

        assert delta.delta == -20.0
        assert delta.absolute_loss == 20.0  # abs value
        assert delta.squared_loss == 400.0

    def test_to_dict(self):
        """Test delta serialization."""
        delta = CostDelta(
            timestamp=datetime.now(),
            period="2026-02",
            actual=329.50,
            estimated=45.00,
            delta=284.50,
            percentage_error=86.3,
        )

        data = delta.to_dict()
        assert data["period"] == "2026-02"
        assert data["actual"] == 329.50
        assert data["absolute_loss"] == 284.50
        assert "squared_loss" in data
