"""S3 bucket ownership verification for safe operations.

Provides tag-based verification to prevent developers from accidentally
modifying each other's AWS resources.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..bootstrap.config import BootstrapConfig


@dataclass
class BucketVerificationResult:
    """Result of bucket ownership verification."""

    bucket_name: str
    exists: bool = False
    accessible: bool = False
    tags_match: bool = False
    actual_tags: dict[str, str] = field(default_factory=dict)
    expected_tags: dict[str, str] = field(default_factory=dict)
    mismatched_tags: dict[str, tuple[str, str]] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def verified(self) -> bool:
        """Check if bucket is fully verified for safe operations."""
        return self.exists and self.accessible and self.tags_match

    def format_report(self) -> str:
        """Format a human-readable verification report."""
        lines = [f"S3 Bucket Verification: {self.bucket_name}"]
        lines.append("")

        if self.error:
            lines.append(f"❌ Error: {self.error}")
            return "\n".join(lines)

        if not self.exists:
            lines.append("❌ Bucket does not exist")
            return "\n".join(lines)

        if not self.accessible:
            lines.append("❌ Bucket not accessible (permission denied)")
            return "\n".join(lines)

        if self.tags_match:
            lines.append("✅ Tag verification PASSED")
            lines.append("")
            lines.append("Tags:")
            for key, value in sorted(self.expected_tags.items()):
                lines.append(f"  {key}: {value}")
        else:
            lines.append("❌ Tag verification FAILED")
            lines.append("")
            if self.mismatched_tags:
                lines.append("Mismatched tags:")
                for key, (expected, actual) in sorted(self.mismatched_tags.items()):
                    lines.append(f"  {key}: expected '{expected}', got '{actual}'")
            lines.append("")
            lines.append("Refusing to operate on bucket without matching tags.")
            lines.append("Use --skip-verify to override (DANGEROUS).")

        return "\n".join(lines)


def get_bucket_tags(
    bucket_name: str,
    profile: str = "default",
    region: str = "us-east-1",
) -> tuple[bool, bool, dict[str, str], Optional[str]]:
    """Get tags from an S3 bucket.

    Args:
        bucket_name: Name of the S3 bucket.
        profile: AWS profile to use.
        region: AWS region.

    Returns:
        Tuple of (exists, accessible, tags, error_message).
    """
    # First check if bucket exists
    try:
        cmd = [
            "aws", "s3api", "head-bucket",
            "--bucket", bucket_name,
            "--region", region,
        ]
        if profile != "default":
            cmd.extend(["--profile", profile])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "404" in stderr or "not found" in stderr:
                return False, False, {}, None
            if "403" in stderr or "forbidden" in stderr or "access denied" in stderr:
                return True, False, {}, "Access denied"
            return False, False, {}, result.stderr.strip()

    except subprocess.TimeoutExpired:
        return False, False, {}, "Timeout checking bucket"
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return False, False, {}, str(e)

    # Get bucket tags
    try:
        cmd = [
            "aws", "s3api", "get-bucket-tagging",
            "--bucket", bucket_name,
            "--region", region,
        ]
        if profile != "default":
            cmd.extend(["--profile", profile])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # NoSuchTagSet means bucket exists but has no tags
            if "NoSuchTagSet" in result.stderr:
                return True, True, {}, None
            return True, True, {}, result.stderr.strip()

        # Parse tags
        data = json.loads(result.stdout)
        tags = {t["Key"]: t["Value"] for t in data.get("TagSet", [])}
        return True, True, tags, None

    except json.JSONDecodeError as e:
        return True, True, {}, f"Invalid JSON response: {e}"
    except subprocess.TimeoutExpired:
        return True, True, {}, "Timeout getting tags"
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return True, True, {}, str(e)


def verify_bucket_ownership(
    bucket_name: str,
    expected_tags: dict[str, str],
    profile: str = "default",
    region: str = "us-east-1",
) -> BucketVerificationResult:
    """Verify bucket exists and has expected ownership tags.

    Checks for required tags:
    - ManagedBy: should be "opentofu"
    - Owner: should match developer email

    Args:
        bucket_name: Name of the S3 bucket.
        expected_tags: Dict of tag key/value pairs to verify.
        profile: AWS profile to use.
        region: AWS region.

    Returns:
        BucketVerificationResult with verification details.
    """
    exists, accessible, actual_tags, error = get_bucket_tags(
        bucket_name, profile, region
    )

    result = BucketVerificationResult(
        bucket_name=bucket_name,
        exists=exists,
        accessible=accessible,
        actual_tags=actual_tags,
        expected_tags=expected_tags,
        error=error,
    )

    if not exists or not accessible or error:
        return result

    # Check each expected tag
    mismatched = {}
    for key, expected_value in expected_tags.items():
        actual_value = actual_tags.get(key, "")
        if actual_value != expected_value:
            mismatched[key] = (expected_value, actual_value)

    result.mismatched_tags = mismatched
    result.tags_match = len(mismatched) == 0

    return result


def verify_developer_bucket(
    config: BootstrapConfig,
    bucket_name: Optional[str] = None,
) -> BucketVerificationResult:
    """Verify the current developer's bucket from config.

    Args:
        config: BootstrapConfig with AWS settings.
        bucket_name: Optional bucket name override.

    Returns:
        BucketVerificationResult with verification details.
    """
    from ..bootstrap.identity import get_aws_bucket_name, get_developer_email

    if bucket_name is None:
        project = getattr(config, "aws_project", "cybersec")
        bucket_name = get_aws_bucket_name(project, config)

    # Ensure bucket_name is a string
    assert bucket_name is not None

    profile = getattr(config, "aws_profile", "default")
    region = getattr(config, "aws_region", "us-east-1")

    expected_tags = {
        "ManagedBy": "opentofu",
        "Owner": get_developer_email(config),
    }

    return verify_bucket_ownership(bucket_name, expected_tags, profile, region)


def empty_bucket_safely(
    bucket_name: str,
    profile: str = "default",
    region: str = "us-east-1",
    dry_run: bool = True,
    skip_verify: bool = False,
    expected_tags: Optional[dict[str, str]] = None,
) -> tuple[bool, str]:
    """Empty an S3 bucket with safety verification.

    Args:
        bucket_name: Name of the S3 bucket.
        profile: AWS profile to use.
        region: AWS region.
        dry_run: If True, only show what would be deleted.
        skip_verify: If True, skip tag verification (DANGEROUS).
        expected_tags: Tags to verify if skip_verify is False.

    Returns:
        Tuple of (success, message).
    """
    # Verify ownership unless skipped
    if not skip_verify:
        if expected_tags is None:
            return False, "expected_tags required when skip_verify is False"

        result = verify_bucket_ownership(bucket_name, expected_tags, profile, region)
        if not result.verified:
            return False, result.format_report()

    # Count objects
    try:
        cmd = [
            "aws", "s3", "ls",
            f"s3://{bucket_name}",
            "--recursive",
            "--region", region,
        ]
        if profile != "default":
            cmd.extend(["--profile", profile])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            return False, f"Failed to list bucket: {result.stderr}"

        # Count lines (each line is an object)
        object_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0

    except subprocess.TimeoutExpired:
        return False, "Timeout listing bucket contents"
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return False, f"Error listing bucket: {e}"

    if dry_run:
        return True, f"DRY RUN: Would delete ~{object_count} objects from s3://{bucket_name}"

    # Delete all object versions
    try:
        # Step 1: Delete versions
        cmd = [
            "aws", "s3api", "list-object-versions",
            "--bucket", bucket_name,
            "--region", region,
            "--output", "json",
        ]
        if profile != "default":
            cmd.extend(["--profile", profile])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)

            # Delete versions
            for version in data.get("Versions", []):
                key = version.get("Key")
                version_id = version.get("VersionId")
                if key and version_id:
                    del_cmd = [
                        "aws", "s3api", "delete-object",
                        "--bucket", bucket_name,
                        "--key", key,
                        "--version-id", version_id,
                        "--region", region,
                    ]
                    if profile != "default":
                        del_cmd.extend(["--profile", profile])
                    subprocess.run(del_cmd, capture_output=True, timeout=30)

            # Delete markers
            for marker in data.get("DeleteMarkers", []):
                key = marker.get("Key")
                version_id = marker.get("VersionId")
                if key and version_id:
                    del_cmd = [
                        "aws", "s3api", "delete-object",
                        "--bucket", bucket_name,
                        "--key", key,
                        "--version-id", version_id,
                        "--region", region,
                    ]
                    if profile != "default":
                        del_cmd.extend(["--profile", profile])
                    subprocess.run(del_cmd, capture_output=True, timeout=30)

        # Step 2: Final cleanup with s3 rm
        cmd = [
            "aws", "s3", "rm",
            f"s3://{bucket_name}",
            "--recursive",
            "--region", region,
        ]
        if profile != "default":
            cmd.extend(["--profile", profile])

        subprocess.run(cmd, capture_output=True, timeout=300)

        return True, f"Successfully emptied s3://{bucket_name}"

    except subprocess.TimeoutExpired:
        return False, "Timeout during bucket empty operation"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON response: {e}"
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return False, f"Error emptying bucket: {e}"
