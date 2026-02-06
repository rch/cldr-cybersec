"""AWS cost API client with graceful degradation.

This module provides wrappers around boto3 AWS API calls with
proper error handling when Cost Explorer access is denied.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import CostConfig

logger = logging.getLogger(__name__)


class AWSCostClient:
    """AWS cost API client with graceful degradation on permission errors.

    Uses real AWS credentials (not MinIO). The devenv now separates
    MINIO_* credentials from AWS credentials.
    """

    def __init__(self, config: CostConfig):
        """Initialize AWS client.

        Args:
            config: Cost tracking configuration
        """
        self.config = config
        self._session = None
        self._ce = None
        self._ec2 = None
        self._pricing = None
        self._sts = None

    @property
    def session(self):
        """Get or create boto3 session."""
        if self._session is None:
            import boto3
            self._session = boto3.Session(
                profile_name=self.config.aws_profile,
                region_name=self.config.aws_region,
            )
        return self._session

    @property
    def ce(self):
        """Cost Explorer client."""
        if self._ce is None:
            self._ce = self.session.client('ce')
        return self._ce

    @property
    def ec2(self):
        """EC2 client."""
        if self._ec2 is None:
            self._ec2 = self.session.client('ec2')
        return self._ec2

    @property
    def pricing(self):
        """AWS Pricing client (always us-east-1)."""
        if self._pricing is None:
            self._pricing = self.session.client('pricing', region_name='us-east-1')
        return self._pricing

    @property
    def sts(self):
        """STS client for account info."""
        if self._sts is None:
            self._sts = self.session.client('sts')
        return self._sts

    def get_account_id(self) -> str:
        """Get current AWS account ID.

        Returns:
            12-digit AWS account ID
        """
        try:
            return self.sts.get_caller_identity()['Account']
        except Exception as e:
            logger.warning(f"Failed to get account ID: {e}")
            return "unknown"

    def get_mtd_cost(self) -> Optional[float]:
        """Get month-to-date cost from Cost Explorer.

        Returns:
            Total unblended cost in USD, or None if access denied
        """
        try:
            today = datetime.now(timezone.utc)
            start = today.replace(day=1).strftime('%Y-%m-%d')
            # End date is exclusive, so use today
            end = today.strftime('%Y-%m-%d')

            response = self.ce.get_cost_and_usage(
                TimePeriod={
                    'Start': start,
                    'End': end,
                },
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
            )

            results = response.get('ResultsByTime', [])
            if results:
                amount = results[0].get('Total', {}).get('UnblendedCost', {}).get('Amount', '0')
                return float(amount)
            return 0.0

        except Exception as e:
            if 'AccessDenied' in str(e) or 'not authorized' in str(e).lower():
                logger.warning("Cost Explorer access denied - using estimation fallback")
                return None
            logger.error(f"Cost Explorer error: {e}")
            raise

    def get_cost_by_service(self) -> dict[str, float]:
        """Get costs grouped by service for current month.

        Returns:
            Dictionary mapping service name to cost in USD
        """
        try:
            today = datetime.now(timezone.utc)
            start = today.replace(day=1).strftime('%Y-%m-%d')
            end = today.strftime('%Y-%m-%d')

            response = self.ce.get_cost_and_usage(
                TimePeriod={
                    'Start': start,
                    'End': end,
                },
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
            )

            costs = {}
            for result in response.get('ResultsByTime', []):
                for group in result.get('Groups', []):
                    service = group['Keys'][0]
                    amount = float(group['Metrics']['UnblendedCost']['Amount'])
                    costs[service] = costs.get(service, 0) + amount

            return costs

        except Exception as e:
            if 'AccessDenied' in str(e):
                logger.warning("Cost by service access denied")
                return {}
            logger.error(f"Cost by service error: {e}")
            return {}

    def get_forecast(self) -> Optional[float]:
        """Get AWS cost forecast for rest of month.

        Returns:
            Forecasted total cost in USD, or None if unavailable
        """
        try:
            today = datetime.now(timezone.utc)
            # Start from today
            start = today.strftime('%Y-%m-%d')
            # End at first of next month
            if today.month == 12:
                end_date = today.replace(year=today.year + 1, month=1, day=1)
            else:
                end_date = today.replace(month=today.month + 1, day=1)
            end = end_date.strftime('%Y-%m-%d')

            response = self.ce.get_cost_forecast(
                TimePeriod={
                    'Start': start,
                    'End': end,
                },
                Metric='UNBLENDED_COST',
                Granularity='MONTHLY',
            )

            total = response.get('Total', {}).get('Amount')
            if total:
                return float(total)
            return None

        except Exception as e:
            if 'AccessDenied' in str(e):
                logger.warning("Cost forecast access denied")
                return None
            # Some accounts don't have enough data for forecasting
            if 'data points' in str(e).lower():
                logger.info("Not enough data for cost forecast")
                return None
            logger.warning(f"Cost forecast error: {e}")
            return None

    def get_running_instances(self) -> list[dict]:
        """Get list of running EC2 instances.

        Returns:
            List of instance dictionaries with type, launch time, etc.
        """
        try:
            response = self.ec2.describe_instances(
                Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
            )

            instances = []
            for reservation in response.get('Reservations', []):
                for instance in reservation.get('Instances', []):
                    instances.append({
                        'instance_id': instance.get('InstanceId'),
                        'instance_type': instance.get('InstanceType'),
                        'launch_time': instance.get('LaunchTime'),
                        'availability_zone': instance.get('Placement', {}).get('AvailabilityZone'),
                        'tags': {t['Key']: t['Value'] for t in instance.get('Tags', [])},
                    })

            return instances

        except Exception as e:
            logger.error(f"Failed to get running instances: {e}")
            return []

    def get_ebs_volumes(self) -> list[dict]:
        """Get list of EBS volumes.

        Returns:
            List of volume dictionaries with size, type, etc.
        """
        try:
            response = self.ec2.describe_volumes()

            volumes = []
            for volume in response.get('Volumes', []):
                volumes.append({
                    'volume_id': volume.get('VolumeId'),
                    'size_gb': volume.get('Size'),
                    'volume_type': volume.get('VolumeType'),
                    'state': volume.get('State'),
                    'iops': volume.get('Iops'),
                    'throughput': volume.get('Throughput'),
                })

            return volumes

        except Exception as e:
            logger.error(f"Failed to get EBS volumes: {e}")
            return []

    def get_nat_gateways(self) -> list[dict]:
        """Get list of NAT gateways.

        Returns:
            List of NAT gateway dictionaries
        """
        try:
            response = self.ec2.describe_nat_gateways(
                Filters=[{'Name': 'state', 'Values': ['available']}]
            )

            nats = []
            for nat in response.get('NatGateways', []):
                nats.append({
                    'nat_gateway_id': nat.get('NatGatewayId'),
                    'state': nat.get('State'),
                    'subnet_id': nat.get('SubnetId'),
                    'create_time': nat.get('CreateTime'),
                })

            return nats

        except Exception as e:
            logger.error(f"Failed to get NAT gateways: {e}")
            return []

    def get_elastic_ips(self) -> list[dict]:
        """Get list of Elastic IP addresses.

        Returns:
            List of EIP dictionaries
        """
        try:
            response = self.ec2.describe_addresses()

            eips = []
            for addr in response.get('Addresses', []):
                eips.append({
                    'allocation_id': addr.get('AllocationId'),
                    'public_ip': addr.get('PublicIp'),
                    'associated': addr.get('AssociationId') is not None,
                })

            return eips

        except Exception as e:
            logger.error(f"Failed to get Elastic IPs: {e}")
            return []
