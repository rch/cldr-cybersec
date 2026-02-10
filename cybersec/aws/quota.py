"""AWS quota validation for fail-fast deployment checks.

This module validates AWS service quotas before deployment to fail fast
instead of discovering limits mid-deployment (e.g., EIP allocation failure
after EC2 instances are already created).

Quotas checked:
- Elastic IPs (ec2/L-0263D0A3): Default 5 per region
- VPCs (vpc/L-F678F1CE): Default 5 per region
- EC2 On-Demand instances: Various by instance type

Usage:
    from cybersec.aws.quota import check_deployment_quotas, QuotaCheckResult

    result = await check_deployment_quotas(region="us-east-1", required_eips=1)
    if not result.success:
        print(f"Quota check failed: {result.errors}")
"""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any


# AWS Service Quotas codes
EIP_QUOTA_CODE = "L-0263D0A3"  # EC2-VPC Elastic IPs
VPC_QUOTA_CODE = "L-F678F1CE"  # VPCs per region


@dataclass
class QuotaInfo:
    """Information about a single quota."""

    name: str
    code: str
    current_usage: int
    limit: int
    required: int
    available: int

    @property
    def sufficient(self) -> bool:
        """Check if quota is sufficient for required amount."""
        return self.available >= self.required

    @property
    def usage_percent(self) -> float:
        """Get usage percentage."""
        if self.limit == 0:
            return 100.0
        return (self.current_usage / self.limit) * 100


@dataclass
class EipInfo:
    """Information about an Elastic IP."""

    public_ip: str
    allocation_id: str
    association_id: str | None
    instance_id: str | None
    network_interface_id: str | None
    name: str
    tags: dict[str, str]

    @property
    def is_associated(self) -> bool:
        """Check if EIP is associated with a resource."""
        return self.association_id is not None

    @property
    def is_unused(self) -> bool:
        """Check if EIP is completely unused (no association)."""
        return not self.is_associated


@dataclass
class QuotaCheckResult:
    """Result from quota validation."""

    success: bool
    region: str
    quotas: dict[str, QuotaInfo] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unused_eips: list[EipInfo] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)

    def format_report(self) -> str:
        """Format a human-readable report."""
        lines = [
            f"AWS Quota Check: {self.region}",
            "=" * 50,
            "",
        ]

        if self.quotas:
            lines.append("Resource Quotas:")
            lines.append("-" * 50)
            lines.append(f"{'Resource':<20} {'Used':<8} {'Limit':<8} {'Avail':<8} {'Need':<8} {'Status'}")
            lines.append("-" * 50)

            for name, quota in self.quotas.items():
                status = "✓ OK" if quota.sufficient else "✗ FAIL"
                lines.append(
                    f"{name:<20} {quota.current_usage:<8} {quota.limit:<8} "
                    f"{quota.available:<8} {quota.required:<8} {status}"
                )

        # Show unused EIPs that could be released
        if self.unused_eips:
            lines.append("")
            lines.append(f"Unused EIPs ({len(self.unused_eips)} found - costing ~${len(self.unused_eips) * 0.005 * 24 * 30:.2f}/month):")
            lines.append("-" * 50)
            for eip in self.unused_eips:
                owner = eip.tags.get("Owner", "unknown")
                name = eip.name or "-"
                lines.append(f"  {eip.public_ip:<16} {eip.allocation_id:<28} {name} (Owner: {owner})")
            lines.append("")
            lines.append("  Release with: aws ec2 release-address --allocation-id <id> --region " + self.region)

        if self.errors:
            lines.append("")
            lines.append("ERRORS:")
            for error in self.errors:
                lines.append(f"  ✗ {error}")

        if self.warnings:
            lines.append("")
            lines.append("WARNINGS:")
            for warning in self.warnings:
                lines.append(f"  ⚠ {warning}")

        lines.append("")
        if self.success:
            lines.append("Result: PASS - Sufficient quota for deployment")
        else:
            lines.append("Result: FAIL - Insufficient quota, deployment will fail")

        return "\n".join(lines)


async def get_eip_usage(
    region: str,
    profile: str | None = None,
) -> tuple[int, str | None]:
    """Get current Elastic IP usage in a region.

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        Tuple of (usage_count, error_message)
    """
    cmd = [
        "aws", "ec2", "describe-addresses",
        "--region", region,
        "--query", "length(Addresses)",
        "--output", "text",
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return 0, f"Failed to get EIP usage: {proc.stderr.strip()}"

        count = int(proc.stdout.strip())
        return count, None

    except subprocess.TimeoutExpired:
        return 0, "Timeout getting EIP usage"
    except ValueError as e:
        return 0, f"Invalid EIP count response: {e}"
    except Exception as e:
        return 0, f"Error getting EIP usage: {e}"


async def get_eip_limit(
    region: str,
    profile: str | None = None,
) -> tuple[int, str | None]:
    """Get Elastic IP quota limit for a region.

    Uses Service Quotas API, falls back to default of 5.

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        Tuple of (limit, error_message)
    """
    cmd = [
        "aws", "service-quotas", "get-service-quota",
        "--service-code", "ec2",
        "--quota-code", EIP_QUOTA_CODE,
        "--region", region,
        "--output", "json",
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            # Service Quotas API may not be available; use default
            return 5, None

        data = json.loads(proc.stdout)
        limit = int(data.get("Quota", {}).get("Value", 5))
        return limit, None

    except subprocess.TimeoutExpired:
        return 5, "Timeout getting quota (using default: 5)"
    except json.JSONDecodeError:
        return 5, "Invalid quota response (using default: 5)"
    except Exception as e:
        return 5, f"Error getting quota (using default: 5): {e}"


async def get_vpc_usage(
    region: str,
    profile: str | None = None,
) -> tuple[int, str | None]:
    """Get current VPC count in a region.

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        Tuple of (usage_count, error_message)
    """
    cmd = [
        "aws", "ec2", "describe-vpcs",
        "--region", region,
        "--query", "length(Vpcs)",
        "--output", "text",
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return 0, f"Failed to get VPC count: {proc.stderr.strip()}"

        count = int(proc.stdout.strip())
        return count, None

    except subprocess.TimeoutExpired:
        return 0, "Timeout getting VPC count"
    except ValueError as e:
        return 0, f"Invalid VPC count response: {e}"
    except Exception as e:
        return 0, f"Error getting VPC count: {e}"


async def get_vpc_limit(
    region: str,
    profile: str | None = None,
) -> tuple[int, str | None]:
    """Get VPC quota limit for a region.

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        Tuple of (limit, error_message)
    """
    cmd = [
        "aws", "service-quotas", "get-service-quota",
        "--service-code", "vpc",
        "--quota-code", VPC_QUOTA_CODE,
        "--region", region,
        "--output", "json",
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return 5, None  # Default VPC limit

        data = json.loads(proc.stdout)
        limit = int(data.get("Quota", {}).get("Value", 5))
        return limit, None

    except subprocess.TimeoutExpired:
        return 5, "Timeout getting quota (using default: 5)"
    except Exception as e:
        return 5, f"Error getting quota (using default: 5): {e}"


async def check_eip_quota(
    region: str,
    required: int = 1,
    profile: str | None = None,
) -> QuotaInfo:
    """Check if sufficient EIPs are available.

    Args:
        region: AWS region
        required: Number of EIPs needed for deployment
        profile: Optional AWS profile

    Returns:
        QuotaInfo with current usage, limit, and availability
    """
    usage, _ = await get_eip_usage(region, profile)
    limit, _ = await get_eip_limit(region, profile)

    available = max(0, limit - usage)

    return QuotaInfo(
        name="Elastic IPs",
        code=EIP_QUOTA_CODE,
        current_usage=usage,
        limit=limit,
        required=required,
        available=available,
    )


async def check_vpc_quota(
    region: str,
    required: int = 1,
    profile: str | None = None,
) -> QuotaInfo:
    """Check if sufficient VPCs are available.

    Args:
        region: AWS region
        required: Number of VPCs needed for deployment
        profile: Optional AWS profile

    Returns:
        QuotaInfo with current usage, limit, and availability
    """
    usage, _ = await get_vpc_usage(region, profile)
    limit, _ = await get_vpc_limit(region, profile)

    available = max(0, limit - usage)

    return QuotaInfo(
        name="VPCs",
        code=VPC_QUOTA_CODE,
        current_usage=usage,
        limit=limit,
        required=required,
        available=available,
    )


async def get_unused_eips(
    region: str,
    profile: str | None = None,
) -> list[EipInfo]:
    """Get Elastic IPs that are not associated with any resource.

    Unassociated EIPs:
    - Cost ~$0.005/hour (~$3.60/month each)
    - Consume quota unnecessarily
    - Can be released to free up quota

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        List of unassociated EipInfo objects
    """
    eips = await list_eips(region, profile)
    unused = []

    for eip in eips:
        # EIP is unused if it has no AssociationId
        if eip.get("AssociationId") is None:
            tags = {t["Key"]: t["Value"] for t in eip.get("Tags", [])}
            unused.append(EipInfo(
                public_ip=eip.get("PublicIp", ""),
                allocation_id=eip.get("AllocationId", ""),
                association_id=eip.get("AssociationId"),
                instance_id=eip.get("InstanceId"),
                network_interface_id=eip.get("NetworkInterfaceId"),
                name=tags.get("Name", ""),
                tags=tags,
            ))

    return unused


async def check_deployment_quotas(
    region: str,
    required_eips: int = 1,
    required_vpcs: int = 1,
    profile: str | None = None,
) -> QuotaCheckResult:
    """Check all quotas required for deployment.

    This is the main entry point for pre-flight quota validation.

    Args:
        region: AWS region for deployment
        required_eips: Number of Elastic IPs needed (default: 1 for NAT GW)
        required_vpcs: Number of VPCs needed (default: 1)
        profile: Optional AWS profile

    Returns:
        QuotaCheckResult with all quota information
    """
    result = QuotaCheckResult(
        success=True,
        region=region,
    )

    # Check EIP quota
    eip_quota = await check_eip_quota(region, required_eips, profile)
    result.quotas["Elastic IPs"] = eip_quota

    # Find unused EIPs that could be released
    unused_eips = await get_unused_eips(region, profile)
    result.unused_eips = unused_eips

    if not eip_quota.sufficient:
        result.success = False
        result.errors.append(
            f"Insufficient Elastic IPs: need {required_eips}, "
            f"have {eip_quota.available} available "
            f"({eip_quota.current_usage}/{eip_quota.limit} in use)"
        )

        # Suggest releasing unused EIPs if any exist
        if unused_eips:
            result.errors.append(
                f"Found {len(unused_eips)} unused EIP(s) that could be released to free quota"
            )
        else:
            result.errors.append(
                f"Request limit increase: "
                f"aws service-quotas request-service-quota-increase "
                f"--service-code ec2 --quota-code {EIP_QUOTA_CODE} "
                f"--desired-value {eip_quota.limit + required_eips} --region {region}"
            )

    # Warn if there are unused EIPs (even if quota is sufficient)
    if unused_eips and eip_quota.sufficient:
        result.warnings.append(
            f"Found {len(unused_eips)} unused EIP(s) - "
            f"costing ~${len(unused_eips) * 0.005 * 24 * 30:.2f}/month"
        )

    # Warn if EIP usage is high (>60%)
    if eip_quota.sufficient and eip_quota.usage_percent > 60:
        result.warnings.append(
            f"EIP usage at {eip_quota.usage_percent:.0f}% "
            f"({eip_quota.current_usage}/{eip_quota.limit})"
        )

    # Check VPC quota
    vpc_quota = await check_vpc_quota(region, required_vpcs, profile)
    result.quotas["VPCs"] = vpc_quota

    if not vpc_quota.sufficient:
        result.success = False
        result.errors.append(
            f"Insufficient VPCs: need {required_vpcs}, "
            f"have {vpc_quota.available} available "
            f"({vpc_quota.current_usage}/{vpc_quota.limit} in use)"
        )

    return result


async def list_eips(
    region: str,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List all Elastic IPs in a region with details.

    Useful for identifying EIPs that can be released.

    Args:
        region: AWS region
        profile: Optional AWS profile

    Returns:
        List of EIP details
    """
    cmd = [
        "aws", "ec2", "describe-addresses",
        "--region", region,
        "--output", "json",
    ]
    if profile:
        cmd.extend(["--profile", profile])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return []

        data = json.loads(proc.stdout)
        return data.get("Addresses", [])

    except Exception:
        return []
