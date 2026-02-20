"""AWS commands for unified command system.

Provides safe AWS operations with developer isolation through tag verification.

Commands:
    /aws                         - Show developer identity and AWS config
    /aws s3:verify [bucket]      - Verify bucket ownership tags
    /aws s3:empty [bucket]       - Empty bucket with tag verification
"""

from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_aws(cmd: ParsedCommand) -> CommandResult:
    """Show developer identity and AWS configuration.

    Options:
        --json, -j  Output as JSON
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import (
        get_developer_prefix,
        get_developer_email,
        get_git_email,
        get_git_username,
        get_aws_bucket_name,
    )

    settings = SettingsManager()
    config = settings.load()

    # Get identity info
    git_email = get_git_email() or "(not configured)"
    git_username = get_git_username() or "(not configured)"
    prefix = get_developer_prefix(config)
    email = get_developer_email(config)
    bucket = get_aws_bucket_name(config.aws_project, config)

    data = {
        "identity": {
            "git_email": git_email,
            "git_username": git_username,
            "developer_prefix": prefix,
            "developer_email": email,
        },
        "aws": {
            "profile": config.aws_profile,
            "region": config.aws_region,
            "project": config.aws_project,
        },
        "bucket": {
            "name": bucket,
            "expected_tags": {
                "ManagedBy": "opentofu",
                "Owner": email,
                "Project": config.aws_project,
            },
        },
    }

    # Format for human display
    prefix_source = "config" if config.developer_prefix else "auto (git email)"
    email_source = "config" if config.developer_email else "auto (git email)"

    lines = [
        "AWS Developer Identity",
        "=" * 50,
        "",
        "Git Configuration:",
        f"  Email:           {git_email}",
        f"  Username:        {git_username}",
        "",
        "Developer Identity:",
        f"  Prefix:          {prefix} ({prefix_source})",
        f"  Email:           {email} ({email_source})",
        "",
        "AWS Configuration:",
        f"  Profile:         {config.aws_profile}",
        f"  Region:          {config.aws_region}",
        f"  Project:         {config.aws_project}",
        "",
        "S3 Bucket:",
        f"  Name:            {bucket}",
        f"  Expected tags:",
        f"    ManagedBy:     opentofu",
        f"    Owner:         {email}",
        f"    Project:       {config.aws_project}",
    ]

    return CommandResult(
        success=True,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_aws_s3_verify(cmd: ParsedCommand) -> CommandResult:
    """Verify S3 bucket ownership tags.

    Usage:
        /aws s3:verify              Verify configured bucket
        /aws s3:verify my-bucket    Verify specific bucket

    Options:
        --json, -j  Output as JSON
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import get_aws_bucket_name, get_developer_email
    from ..aws.s3_safety import verify_bucket_ownership

    settings = SettingsManager()
    config = settings.load()

    # Get bucket from args or config
    bucket_name = cmd.args[0] if cmd.args else None
    if bucket_name is None:
        bucket_name = get_aws_bucket_name(config.aws_project, config)

    email = get_developer_email(config)
    expected_tags = {
        "ManagedBy": "opentofu",
        "Owner": email,
    }

    result = verify_bucket_ownership(
        bucket_name,
        expected_tags,
        profile=config.aws_profile,
        region=config.aws_region,
    )

    data = {
        "bucket_name": result.bucket_name,
        "exists": result.exists,
        "accessible": result.accessible,
        "tags_match": result.tags_match,
        "verified": result.verified,
        "actual_tags": result.actual_tags,
        "expected_tags": result.expected_tags,
        "mismatched_tags": {
            k: {"expected": v[0], "actual": v[1]}
            for k, v in result.mismatched_tags.items()
        },
        "error": result.error,
    }

    return CommandResult(
        success=result.verified,
        data=data,
        formatted=result.format_report(),
    )


async def cmd_aws_s3_empty(cmd: ParsedCommand) -> CommandResult:
    """Empty S3 bucket with tag verification.

    SAFETY: Verifies ManagedBy=opentofu and Owner tags before emptying.
    This prevents accidentally emptying another developer's bucket.

    Usage:
        /aws s3:empty               Empty configured bucket (dry-run)
        /aws s3:empty --apply       Actually empty the bucket
        /aws s3:empty my-bucket     Empty specific bucket (dry-run)

    Options:
        --apply         Actually delete objects (default is dry-run)
        --skip-verify   Skip tag verification (DANGEROUS)
        --force         Skip confirmation prompt
        --json, -j      Output as JSON
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import get_aws_bucket_name, get_developer_email
    from ..aws.s3_safety import empty_bucket_safely

    settings = SettingsManager()
    config = settings.load()

    # Get bucket from args or config
    bucket_name = cmd.args[0] if cmd.args else None
    if bucket_name is None:
        bucket_name = get_aws_bucket_name(config.aws_project, config)

    # Parse options
    apply = cmd.options.get("apply", False)
    skip_verify = cmd.options.get("skip_verify", False)
    dry_run = not apply

    email = get_developer_email(config)
    expected_tags = {
        "ManagedBy": "opentofu",
        "Owner": email,
    }

    # Add warning header if skipping verification
    lines = []
    if skip_verify:
        lines.extend([
            "WARNING: Tag verification SKIPPED (--skip-verify)",
            "This may affect buckets owned by other developers!",
            "",
        ])

    success, message = empty_bucket_safely(
        bucket_name,
        profile=config.aws_profile,
        region=config.aws_region,
        dry_run=dry_run,
        skip_verify=skip_verify,
        expected_tags=expected_tags if not skip_verify else None,
    )

    data = {
        "bucket_name": bucket_name,
        "success": success,
        "dry_run": dry_run,
        "skip_verify": skip_verify,
        "message": message,
    }

    lines.append(message)

    if dry_run and success:
        lines.extend([
            "",
            "Use --apply to actually delete objects.",
        ])

    return CommandResult(
        success=success,
        data=data,
        formatted="\n".join(lines),
    )


async def cmd_aws_preflight(cmd: ParsedCommand) -> CommandResult:
    """Pre-flight quota validation before AWS deployment.

    Checks resource quotas to fail fast before deployment starts.
    This prevents partial deployments that fail mid-way due to quota limits.
    Also checks for existing resources owned by the developer.

    Usage:
        /aws preflight              Check quotas in configured region
        /aws preflight us-west-1    Check quotas in specific region
        /aws preflight --eips       Show current EIP allocations

    Options:
        --eips      List current Elastic IP allocations
        --json, -j  Output as JSON
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import get_developer_email
    from ..aws.quota import check_deployment_quotas, list_eips, find_owned_resources

    settings = SettingsManager()
    config = settings.load()

    # Get region from args or config
    region = cmd.args[0] if cmd.args else config.aws_region

    # Handle --eips option to list current allocations
    if cmd.options.get("eips", False):
        eips = await list_eips(region, config.aws_profile)

        data = {
            "region": region,
            "eip_count": len(eips),
            "eips": [
                {
                    "public_ip": eip.get("PublicIp"),
                    "allocation_id": eip.get("AllocationId"),
                    "association_id": eip.get("AssociationId"),
                    "instance_id": eip.get("InstanceId"),
                    "network_interface_id": eip.get("NetworkInterfaceId"),
                    "tags": {t["Key"]: t["Value"] for t in eip.get("Tags", [])},
                }
                for eip in eips
            ],
        }

        lines = [
            f"Elastic IPs in {region}",
            "=" * 60,
            "",
        ]

        if eips:
            lines.append(f"{'Public IP':<16} {'Instance':<22} {'Name/Tags'}")
            lines.append("-" * 60)
            for eip in eips:
                public_ip = eip.get("PublicIp", "N/A")
                instance_id = eip.get("InstanceId", "-")
                tags = {t["Key"]: t["Value"] for t in eip.get("Tags", [])}
                name = tags.get("Name", "-")
                lines.append(f"{public_ip:<16} {instance_id:<22} {name}")
        else:
            lines.append("No Elastic IPs allocated")

        lines.append("")
        lines.append(f"Total: {len(eips)} EIP(s)")

        return CommandResult(
            success=True,
            data=data,
            formatted="\n".join(lines),
        )

    # Check if security logs are enabled (from env or default true)
    import os
    check_security_logs = os.environ.get("TF_VAR_enable_security_logs", "true").lower() == "true"

    # Run quota checks (includes IAM permission probes when security logs enabled)
    result = await check_deployment_quotas(
        region=region,
        required_eips=1,  # NAT gateway
        required_vpcs=1,  # VPC
        profile=config.aws_profile,
        check_security_logs=check_security_logs,
    )

    # Check for existing resources owned by this developer
    email = get_developer_email(config)
    owned = await find_owned_resources(
        region=region,
        owner_email=email,
        project=config.aws_project,
        profile=config.aws_profile,
    )

    data = {
        "success": result.success,
        "region": result.region,
        "quotas": {
            name: {
                "current_usage": quota.current_usage,
                "limit": quota.limit,
                "available": quota.available,
                "required": quota.required,
                "sufficient": quota.sufficient,
            }
            for name, quota in result.quotas.items()
        },
        "unused_eips": [
            {
                "public_ip": eip.public_ip,
                "allocation_id": eip.allocation_id,
                "name": eip.name,
                "tags": eip.tags,
            }
            for eip in result.unused_eips
        ],
        "errors": result.errors,
        "warnings": result.warnings,
        "owned_resources": {
            "owner": owned.owner_email,
            "project": owned.project,
            "count": owned.resource_count,
            "resources": [
                {
                    "type": r.resource_type,
                    "id": r.resource_id,
                    "name": r.name,
                    "state": r.state,
                    "details": r.details,
                }
                for r in owned.resources
            ],
        },
    }

    # Combine reports
    formatted = result.format_report()
    if owned.has_resources:
        formatted += "\n\n" + owned.format_report()

    return CommandResult(
        success=result.success,
        data=data,
        formatted=formatted,
    )


async def cmd_aws_target(cmd: ParsedCommand) -> CommandResult:
    """Set or show AWS target region with validation.

    Usage:
        /aws target                 Show current target configuration
        /aws target us-west-1       Set region to us-west-1
        /aws target --list          List allowed regions

    Options:
        --dry-run   Validate without writing config
        --list      List allowed regions
        --json, -j  Output as JSON
    """
    from ..aws.target import (
        ALLOWED_REGIONS,
        get_current_target,
        set_aws_target,
        list_allowed_regions,
    )

    # Handle --list option
    if cmd.options.get("list", False):
        regions = list_allowed_regions()
        data = {"allowed_regions": regions}
        lines = [
            "Allowed AWS Regions",
            "=" * 30,
            "",
        ]
        for region in regions:
            lines.append(f"  {region}")

        return CommandResult(
            success=True,
            data=data,
            formatted="\n".join(lines),
        )

    # Get region from args
    region = cmd.args[0] if cmd.args else None
    dry_run = cmd.options.get("dry_run", False)

    if region is None:
        # Show current target
        result = await get_current_target()

        data = {
            "success": result.success,
            "region": result.region,
            "bucket_name": result.bucket_name,
            "expected_tags": result.expected_tags,
            "validation": {
                "success": result.validation.success,
                "denies": result.validation.denies,
                "warnings": result.validation.warnings,
            },
        }

        lines = [
            "AWS Target Configuration",
            "=" * 50,
            "",
            f"Region:          {result.region}",
            f"Bucket:          {result.bucket_name}",
            "",
            "Expected Tags:",
        ]
        for key, value in result.expected_tags.items():
            lines.append(f"  {key}: {value}")

        if result.validation.denies:
            lines.append("")
            lines.append("ERRORS:")
            for deny in result.validation.denies:
                lines.append(f"  - {deny}")

        if result.validation.warnings:
            lines.append("")
            lines.append("WARNINGS:")
            for warn in result.validation.warnings:
                lines.append(f"  - {warn}")

        lines.append("")
        if result.success:
            lines.append("Validation: PASSED")
        else:
            lines.append(f"Validation: FAILED ({len(result.validation.denies)} error(s))")

        return CommandResult(
            success=result.success,
            data=data,
            formatted="\n".join(lines),
        )

    # Quick validation before running conftest
    if region not in ALLOWED_REGIONS:
        return CommandResult(
            success=False,
            error=f"Region '{region}' not allowed. Use /aws target --list to see allowed regions.",
        )

    # Set target region
    result = await set_aws_target(region, dry_run=dry_run)

    data = {
        "success": result.success,
        "region": result.region,
        "bucket_name": result.bucket_name,
        "expected_tags": result.expected_tags,
        "config_path": result.config_path,
        "validation": {
            "success": result.validation.success,
            "denies": result.validation.denies,
            "warnings": result.validation.warnings,
        },
        "message": result.message,
    }

    lines = [
        f"Validating AWS target: {region}",
        "=" * 50,
    ]

    if result.validation.infos:
        lines.append("")
        lines.append("INFO:")
        for info in result.validation.infos:
            lines.append(f"  - {info}")

    if result.validation.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for warn in result.validation.warnings:
            lines.append(f"  - {warn}")

    if result.validation.denies:
        lines.append("")
        lines.append("ERRORS:")
        for deny in result.validation.denies:
            lines.append(f"  - {deny}")

    lines.append("")
    if result.success:
        if dry_run:
            lines.append("DRY RUN: Validation PASSED. Run without --dry-run to update config.")
        else:
            lines.append(f"Validation PASSED. Config updated.")
            lines.append(f"AWS region set to: {region}")
            lines.append("")
            lines.append(f"Bucket: {result.bucket_name}")
    else:
        lines.append(f"Validation FAILED. Config not updated.")

    return CommandResult(
        success=result.success,
        data=data,
        formatted="\n".join(lines),
    )


def register_aws_commands():
    """Register all AWS commands."""
    register_command(
        "aws",
        cmd_aws,
        description="Show developer identity and AWS configuration",
        examples=["/aws", "/aws --json"],
    )

    register_command(
        "aws.s3:verify",
        cmd_aws_s3_verify,
        description="Verify S3 bucket ownership tags",
        args=[{"name": "bucket", "required": False, "help": "Bucket name (default: from config)"}],
        examples=[
            "/aws s3:verify",
            "/aws s3:verify my-bucket",
            "/aws s3:verify --json",
        ],
    )

    register_command(
        "aws.s3:empty",
        cmd_aws_s3_empty,
        description="Empty S3 bucket with tag verification",
        args=[{"name": "bucket", "required": False, "help": "Bucket name (default: from config)"}],
        options=[
            {"name": "apply", "help": "Actually delete objects (default is dry-run)"},
            {"name": "skip-verify", "help": "Skip tag verification (DANGEROUS)"},
            {"name": "force", "help": "Skip confirmation prompt"},
        ],
        examples=[
            "/aws s3:empty",
            "/aws s3:empty --apply",
            "/aws s3:empty my-bucket --apply",
            "/aws s3:empty --skip-verify --apply",
        ],
    )

    register_command(
        "aws.target",
        cmd_aws_target,
        description="Set or show AWS target region",
        args=[{"name": "region", "required": False, "help": "Target region (e.g., us-west-1)"}],
        options=[
            {"name": "dry-run", "help": "Validate without writing config"},
            {"name": "list", "help": "List allowed regions"},
        ],
        examples=[
            "/aws target",
            "/aws target us-west-1",
            "/aws target --list",
            "/aws target us-west-1 --dry-run",
        ],
    )

    register_command(
        "aws.preflight",
        cmd_aws_preflight,
        description="Pre-flight quota validation before deployment",
        args=[{"name": "region", "required": False, "help": "Target region (default: from config)"}],
        options=[
            {"name": "eips", "help": "List current Elastic IP allocations"},
        ],
        examples=[
            "/aws preflight",
            "/aws preflight us-east-1",
            "/aws preflight --eips",
            "/aws preflight us-west-1 --eips",
        ],
    )
