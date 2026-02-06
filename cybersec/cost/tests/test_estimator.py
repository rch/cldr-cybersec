"""Tests for cost estimator module."""

import pytest
from datetime import datetime, timezone

from cybersec.cost.config import CostConfig
from cybersec.cost.estimator import ResourceEstimator


class TestResourceEstimator:
    """Tests for ResourceEstimator class."""

    def test_get_ec2_hourly_rate_known(self):
        """Test known EC2 instance type rate."""
        estimator = ResourceEstimator()

        rate = estimator.get_ec2_hourly_rate("m6i.xlarge")
        assert rate == 0.192

        rate = estimator.get_ec2_hourly_rate("t3.small")
        assert rate == 0.0208

    def test_get_ec2_hourly_rate_unknown(self):
        """Test unknown instance type returns default rate."""
        estimator = ResourceEstimator()

        rate = estimator.get_ec2_hourly_rate("unknown.type")
        assert rate == estimator.DEFAULT_EC2_HOURLY

    def test_get_ebs_hourly_rate(self):
        """Test EBS volume type rates."""
        estimator = ResourceEstimator()

        gp3_rate = estimator.get_ebs_hourly_rate("gp3")
        gp2_rate = estimator.get_ebs_hourly_rate("gp2")

        # gp2 is more expensive than gp3
        assert gp2_rate > gp3_rate

    def test_estimate_ec2_cost(self):
        """Test EC2 cost estimation."""
        from datetime import timedelta

        estimator = ResourceEstimator()

        # Instance launched 100 hours ago
        launch_time = datetime.now(timezone.utc) - timedelta(hours=100)
        instances = [
            {
                "instance_id": "i-12345",
                "instance_type": "m6i.xlarge",
                "launch_time": launch_time,
            }
        ]

        costs = estimator.estimate_ec2_cost(instances, period_hours=100)

        assert len(costs) == 1
        assert costs[0].service == "ec2"
        assert costs[0].resource_type == "m6i.xlarge"
        # m6i.xlarge @ $0.192/hr * 100hrs = $19.20
        assert abs(costs[0].total_cost - 19.20) < 0.01

    def test_estimate_ebs_cost(self):
        """Test EBS cost estimation."""
        estimator = ResourceEstimator()

        volumes = [
            {
                "volume_id": "vol-123",
                "volume_type": "gp3",
                "size_gb": 100,
            }
        ]

        # 730 hours (1 month)
        costs = estimator.estimate_ebs_cost(volumes, period_hours=730)

        assert len(costs) == 1
        assert costs[0].service == "ebs"
        # gp3: $0.08/GB-month, 100GB = $8/month
        assert abs(costs[0].total_cost - 8.0) < 0.5

    def test_estimate_nat_cost(self):
        """Test NAT gateway cost estimation."""
        estimator = ResourceEstimator()

        nats = [
            {
                "nat_gateway_id": "nat-123",
                "state": "available",
            }
        ]

        # 730 hours
        costs = estimator.estimate_nat_cost(nats, period_hours=730)

        assert len(costs) == 1
        assert costs[0].service == "nat"
        # NAT: $0.045/hr * 730 = $32.85
        assert abs(costs[0].total_cost - 32.85) < 0.5

    def test_estimate_eip_unattached(self):
        """Test unattached EIP cost estimation."""
        estimator = ResourceEstimator()

        eips = [
            {
                "allocation_id": "eipalloc-123",
                "public_ip": "1.2.3.4",
                "associated": False,  # Unattached = costs money
            },
            {
                "allocation_id": "eipalloc-456",
                "public_ip": "5.6.7.8",
                "associated": True,  # Attached = free
            },
        ]

        costs = estimator.estimate_eip_cost(eips, period_hours=730)

        # Only unattached EIP should be charged
        assert len(costs) == 1
        assert costs[0].service == "eip"

    def test_pricing_cache_has_common_types(self):
        """Test that pricing cache has common instance types."""
        estimator = ResourceEstimator()

        # Check common types exist
        assert "t3.small" in estimator.PRICING["ec2"]
        assert "m6i.xlarge" in estimator.PRICING["ec2"]
        assert "gp3" in estimator.PRICING["ebs"]
        assert "gateway_hourly" in estimator.PRICING["nat"]
