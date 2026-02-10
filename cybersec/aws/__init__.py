"""AWS utilities for safe resource management.

This module provides safety mechanisms to prevent developers from
accidentally modifying each other's AWS resources through tag verification.
"""

from .s3_safety import (
    BucketVerificationResult,
    verify_bucket_ownership,
    verify_developer_bucket,
    empty_bucket_safely,
)
from .target import (
    ALLOWED_REGIONS,
    ValidationResult,
    AwsTargetResult,
    validate_aws_target,
    set_aws_target,
    get_current_target,
    list_allowed_regions,
)

__all__ = [
    # s3_safety
    "BucketVerificationResult",
    "verify_bucket_ownership",
    "verify_developer_bucket",
    "empty_bucket_safely",
    # target
    "ALLOWED_REGIONS",
    "ValidationResult",
    "AwsTargetResult",
    "validate_aws_target",
    "set_aws_target",
    "get_current_target",
    "list_allowed_regions",
]
