"""Resource inventory-based cost estimation.

This module estimates AWS costs from resource inventory when
Cost Explorer access is unavailable or as a validation baseline.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from .config import CostConfig, CostEstimate, CostSource, ResourceCost
from .aws_client import AWSCostClient

logger = logging.getLogger(__name__)


class ResourceEstimator:
    """Estimate costs from resource inventory.

    Uses a local pricing cache for common resource types. Prices are
    for us-east-1 on-demand rates and may vary by region.
    """

    # Pricing cache: hourly rates in USD (us-east-1 on-demand)
    # Updated periodically from AWS Pricing API
    PRICING = {
        'ec2': {
            # General purpose
            't3.nano': 0.0052,
            't3.micro': 0.0104,
            't3.small': 0.0208,
            't3.medium': 0.0416,
            't3.large': 0.0832,
            't3.xlarge': 0.1664,
            't3.2xlarge': 0.3328,
            # M6i (Intel)
            'm6i.large': 0.096,
            'm6i.xlarge': 0.192,
            'm6i.2xlarge': 0.384,
            'm6i.4xlarge': 0.768,
            'm6i.8xlarge': 1.536,
            # M6a (AMD)
            'm6a.large': 0.0864,
            'm6a.xlarge': 0.1728,
            'm6a.2xlarge': 0.3456,
            'm6a.4xlarge': 0.6912,
            # Compute optimized
            'c6i.large': 0.085,
            'c6i.xlarge': 0.170,
            'c6i.2xlarge': 0.340,
            'c6i.4xlarge': 0.680,
            # Memory optimized
            'r6i.large': 0.126,
            'r6i.xlarge': 0.252,
            'r6i.2xlarge': 0.504,
        },
        'ebs': {
            # Per GB-month (divide by 730 for hourly)
            'gp3': 0.08 / 730,
            'gp2': 0.10 / 730,
            'io1': 0.125 / 730,
            'io2': 0.125 / 730,
            'st1': 0.045 / 730,
            'sc1': 0.015 / 730,
            'standard': 0.05 / 730,
        },
        'nat': {
            # Per hour + per GB processed
            'gateway_hourly': 0.045,
            'data_per_gb': 0.045,
        },
        'eip': {
            # Unattached EIP per hour
            'unattached_hourly': 0.005,
        },
        'nlb': {
            # Network load balancer
            'hourly': 0.0225,
            'lcu': 0.006,
        },
        'alb': {
            # Application load balancer
            'hourly': 0.0225,
            'lcu': 0.008,
        },
        's3': {
            # Per GB-month (divide by 730 for hourly)
            'storage_standard_gb': 0.023 / 730,
            'storage_ia_gb': 0.0125 / 730,
            'requests_1k': 0.0004,
        },
    }

    # Default hourly rate for unknown instance types
    DEFAULT_EC2_HOURLY = 0.10

    def __init__(self, config: Optional[CostConfig] = None):
        """Initialize estimator.

        Args:
            config: Cost configuration (uses defaults if None)
        """
        self.config = config or CostConfig()

    def get_ec2_hourly_rate(self, instance_type: str) -> float:
        """Get hourly rate for EC2 instance type.

        Args:
            instance_type: EC2 instance type (e.g., m6i.xlarge)

        Returns:
            Hourly rate in USD
        """
        return self.PRICING['ec2'].get(instance_type, self.DEFAULT_EC2_HOURLY)

    def get_ebs_hourly_rate(self, volume_type: str) -> float:
        """Get hourly rate per GB for EBS volume type.

        Args:
            volume_type: EBS volume type (e.g., gp3)

        Returns:
            Hourly rate per GB in USD
        """
        return self.PRICING['ebs'].get(volume_type, self.PRICING['ebs']['gp3'])

    def estimate_ec2_cost(
        self,
        instances: list[dict],
        period_hours: float,
    ) -> list[ResourceCost]:
        """Estimate EC2 instance costs.

        Args:
            instances: List of instance dictionaries from AWSCostClient
            period_hours: Number of hours in billing period

        Returns:
            List of ResourceCost for EC2
        """
        resources = []

        for instance in instances:
            instance_type = instance.get('instance_type', 'unknown')
            launch_time = instance.get('launch_time')

            # Calculate hours running
            if launch_time:
                now = datetime.now(timezone.utc)
                if hasattr(launch_time, 'tzinfo') and launch_time.tzinfo is None:
                    launch_time = launch_time.replace(tzinfo=timezone.utc)
                hours = min((now - launch_time).total_seconds() / 3600, period_hours)
            else:
                hours = period_hours

            rate = self.get_ec2_hourly_rate(instance_type)
            cost = hours * rate

            resources.append(ResourceCost(
                service='ec2',
                resource_type=instance_type,
                quantity=1,
                unit_cost_hourly=rate,
                hours=hours,
                total_cost=cost,
                region=self.config.aws_region,
                resource_id=instance.get('instance_id'),
            ))

        return resources

    def estimate_ebs_cost(
        self,
        volumes: list[dict],
        period_hours: float,
    ) -> list[ResourceCost]:
        """Estimate EBS volume costs.

        Args:
            volumes: List of volume dictionaries from AWSCostClient
            period_hours: Number of hours in billing period

        Returns:
            List of ResourceCost for EBS
        """
        resources = []

        for volume in volumes:
            volume_type = volume.get('volume_type', 'gp3')
            size_gb = volume.get('size_gb', 0)

            rate_per_gb = self.get_ebs_hourly_rate(volume_type)
            total_hourly = rate_per_gb * size_gb
            cost = total_hourly * period_hours

            resources.append(ResourceCost(
                service='ebs',
                resource_type=f"{volume_type}-{size_gb}gb",
                quantity=1,
                unit_cost_hourly=total_hourly,
                hours=period_hours,
                total_cost=cost,
                region=self.config.aws_region,
                resource_id=volume.get('volume_id'),
            ))

        return resources

    def estimate_nat_cost(
        self,
        nat_gateways: list[dict],
        period_hours: float,
        data_transfer_gb: float = 0,
    ) -> list[ResourceCost]:
        """Estimate NAT gateway costs.

        Args:
            nat_gateways: List of NAT gateway dictionaries
            period_hours: Number of hours in billing period
            data_transfer_gb: Estimated data transfer in GB

        Returns:
            List of ResourceCost for NAT
        """
        resources = []

        for nat in nat_gateways:
            # Hourly charge
            hourly_cost = self.PRICING['nat']['gateway_hourly'] * period_hours

            # Data transfer (distributed across NATs)
            data_cost = 0
            if nat_gateways:
                data_cost = (data_transfer_gb / len(nat_gateways)) * self.PRICING['nat']['data_per_gb']

            total_cost = hourly_cost + data_cost

            resources.append(ResourceCost(
                service='nat',
                resource_type='gateway',
                quantity=1,
                unit_cost_hourly=self.PRICING['nat']['gateway_hourly'],
                hours=period_hours,
                total_cost=total_cost,
                region=self.config.aws_region,
                resource_id=nat.get('nat_gateway_id'),
            ))

        return resources

    def estimate_eip_cost(
        self,
        eips: list[dict],
        period_hours: float,
    ) -> list[ResourceCost]:
        """Estimate Elastic IP costs.

        Note: Only unattached EIPs incur charges.

        Args:
            eips: List of EIP dictionaries
            period_hours: Number of hours in billing period

        Returns:
            List of ResourceCost for EIPs
        """
        resources = []

        for eip in eips:
            if not eip.get('associated', True):
                cost = self.PRICING['eip']['unattached_hourly'] * period_hours

                resources.append(ResourceCost(
                    service='eip',
                    resource_type='unattached',
                    quantity=1,
                    unit_cost_hourly=self.PRICING['eip']['unattached_hourly'],
                    hours=period_hours,
                    total_cost=cost,
                    region=self.config.aws_region,
                    resource_id=eip.get('allocation_id'),
                ))

        return resources

    def estimate_from_inventory(
        self,
        client: AWSCostClient,
        data_transfer_gb: float = 0,
    ) -> CostEstimate:
        """Build complete cost estimate from resource inventory.

        Args:
            client: AWS client for resource queries
            data_transfer_gb: Estimated data transfer (for NAT)

        Returns:
            CostEstimate with all resources
        """
        now = datetime.now(timezone.utc)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Calculate hours in current billing period
        period_hours = (now - period_start).total_seconds() / 3600

        # Gather resources
        instances = client.get_running_instances()
        volumes = client.get_ebs_volumes()
        nat_gateways = client.get_nat_gateways()
        eips = client.get_elastic_ips()

        # Estimate costs
        resources = []
        resources.extend(self.estimate_ec2_cost(instances, period_hours))
        resources.extend(self.estimate_ebs_cost(volumes, period_hours))
        resources.extend(self.estimate_nat_cost(nat_gateways, period_hours, data_transfer_gb))
        resources.extend(self.estimate_eip_cost(eips, period_hours))

        # Aggregate by service
        by_service: dict[str, float] = {}
        for r in resources:
            by_service[r.service] = by_service.get(r.service, 0) + r.total_cost

        total_cost = sum(r.total_cost for r in resources)

        return CostEstimate(
            timestamp=now,
            period_start=period_start,
            period_end=now,
            source=CostSource.RESOURCE_INVENTORY,
            total_cost=total_cost,
            by_service=by_service,
            resources=resources,
            region=self.config.aws_region,
            account_id=client.get_account_id(),
            confidence="medium",
            notes=f"Estimated from {len(resources)} resources over {period_hours:.1f} hours",
        )
