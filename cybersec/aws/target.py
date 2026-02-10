"""AWS target region validation and configuration.

This module handles:
1. Region validation via conftest policies
2. Target configuration file generation
3. Integration with .cybersec/config.toml

The flow:
    1. Gather runtime config (credentials, identity)
    2. Write build/aws-target.json
    3. Run conftest with policy/aws/target.rego
    4. If valid, update .cybersec/config.toml with new region
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allowed regions for AWS target
ALLOWED_REGIONS = {
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "eu-west-1",
    "eu-central-1",
    "ap-southeast-1",
}


@dataclass
class ValidationResult:
    """Result from conftest policy validation."""

    success: bool
    denies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class AwsTargetResult:
    """Result from AWS target configuration."""

    success: bool
    region: str
    validation: ValidationResult
    config_path: str | None = None
    bucket_name: str = ""
    expected_tags: dict[str, str] = field(default_factory=dict)
    message: str = ""


async def validate_aws_target(
    region: str,
    runtime_config: dict[str, Any] | None = None,
) -> ValidationResult:
    """Validate AWS target using conftest policies.

    Args:
        region: Target AWS region
        runtime_config: Optional pre-gathered runtime config

    Returns:
        ValidationResult with policy check results
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import (
        get_developer_prefix,
        get_developer_email,
        get_aws_bucket_name,
    )
    from ..config.runtime import gather_runtime_config

    if runtime_config is None:
        runtime_config = await gather_runtime_config()

    settings = SettingsManager()
    config = settings.load()

    # Build input for conftest
    prefix = get_developer_prefix(config)
    email = get_developer_email(config)
    bucket = get_aws_bucket_name(config.aws_project, config)

    env_config = {
        "target": {
            "region": region,
        },
        "developer": {
            "prefix": prefix,
            "email": email,
            "bucket_name": bucket,
            "expected_tags": {
                "ManagedBy": "opentofu",
                "Owner": email,
                "Project": config.aws_project,
            },
        },
        "aws": runtime_config.get("aws", {}),
    }

    # Write temporary aws-target.json for conftest
    build_dir = Path("build")
    build_dir.mkdir(exist_ok=True)
    target_file = build_dir / "aws-target.json"
    target_file.write_text(json.dumps(env_config, indent=2))

    # Determine policy path
    project_root = Path(__file__).parent.parent.parent
    policy_dir = project_root / "policy" / "aws"

    # Build conftest command
    cmd = [
        "conftest", "test", str(target_file),
        "--policy", str(policy_dir),
        "--all-namespaces",
        "--output", "json",
    ]

    result = ValidationResult(success=True)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result.raw_output = proc.stdout + proc.stderr

        if proc.stdout:
            try:
                output = json.loads(proc.stdout)
                for item in output:
                    if "failures" in item:
                        for failure in item["failures"]:
                            result.denies.append(failure.get("msg", str(failure)))
                    if "warnings" in item:
                        for warning in item["warnings"]:
                            result.warnings.append(warning.get("msg", str(warning)))
            except json.JSONDecodeError:
                for line in proc.stdout.split("\n"):
                    if "FAIL" in line:
                        result.denies.append(line)
                    elif "WARN" in line:
                        result.warnings.append(line)

        result.success = len(result.denies) == 0

    except FileNotFoundError:
        result.success = False
        result.denies.append("conftest not installed. Install: brew install conftest")
    except subprocess.TimeoutExpired:
        result.success = False
        result.denies.append("conftest timed out")
    except Exception as e:
        result.success = False
        result.denies.append(f"conftest error: {e}")

    return result


async def set_aws_target(
    region: str,
    dry_run: bool = False,
) -> AwsTargetResult:
    """Set AWS target region with validation.

    Args:
        region: Target AWS region
        dry_run: If True, validate but don't write config

    Returns:
        AwsTargetResult with validation and config details
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import get_developer_email, get_aws_bucket_name
    from ..config.runtime import gather_runtime_config

    # Gather runtime config
    runtime_config = await gather_runtime_config()

    # Validate target
    validation = await validate_aws_target(region, runtime_config)

    settings = SettingsManager()
    config = settings.load()
    email = get_developer_email(config)
    bucket = get_aws_bucket_name(config.aws_project, config)

    expected_tags = {
        "ManagedBy": "opentofu",
        "Owner": email,
        "Project": config.aws_project,
    }

    if not validation.success:
        return AwsTargetResult(
            success=False,
            region=region,
            validation=validation,
            bucket_name=bucket,
            expected_tags=expected_tags,
            message=f"Validation failed: {len(validation.denies)} error(s)",
        )

    # Update config if not dry-run
    config_path = None
    if not dry_run:
        config.aws_region = region
        settings.save(config)
        config_path = str(settings.config_path)

    return AwsTargetResult(
        success=True,
        region=region,
        validation=validation,
        config_path=config_path,
        bucket_name=bucket,
        expected_tags=expected_tags,
        message="DRY RUN: Validation passed" if dry_run else f"AWS region set to: {region}",
    )


async def get_current_target() -> AwsTargetResult:
    """Get current AWS target configuration.

    Returns:
        AwsTargetResult with current configuration
    """
    from ..bootstrap.config import SettingsManager
    from ..bootstrap.identity import get_developer_email, get_aws_bucket_name

    settings = SettingsManager()
    config = settings.load()
    email = get_developer_email(config)
    bucket = get_aws_bucket_name(config.aws_project, config)

    expected_tags = {
        "ManagedBy": "opentofu",
        "Owner": email,
        "Project": config.aws_project,
    }

    # Validate current config
    validation = await validate_aws_target(config.aws_region)

    return AwsTargetResult(
        success=validation.success,
        region=config.aws_region,
        validation=validation,
        config_path=str(settings.config_path) if settings.exists() else None,
        bucket_name=bucket,
        expected_tags=expected_tags,
        message="Current AWS target" if validation.success else "Current target has validation issues",
    )


def list_allowed_regions() -> list[str]:
    """Get list of allowed AWS regions.

    Returns:
        Sorted list of allowed region names
    """
    return sorted(ALLOWED_REGIONS)
