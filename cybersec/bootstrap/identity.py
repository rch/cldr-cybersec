"""Developer identity management for AWS resource isolation.

Generates stable developer prefixes for unique bucket naming and
provides utilities for identifying the current developer.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import BootstrapConfig


def get_git_email() -> Optional[str]:
    """Get git user email from git config.

    Returns:
        The configured git email, or None if not configured.
    """
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return None


def get_git_username() -> Optional[str]:
    """Get git user name from git config.

    Returns:
        The configured git username, or None if not configured.
    """
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return None


def get_developer_identity() -> str:
    """Get the developer identity string for hashing.

    Priority order:
    1. Git email (most stable across machines)
    2. $USER environment variable
    3. "unknown" fallback

    Returns:
        Identity string to use for prefix generation.
    """
    return get_git_email() or os.environ.get("USER", "unknown")


def generate_developer_prefix(identity: str) -> str:
    """Generate a stable 8-character prefix from an identity string.

    Uses SHA-256 hash truncated to 8 hex characters for:
    - Stability: same identity always produces same prefix
    - Uniqueness: 16^8 = 4 billion possible prefixes
    - URL-safety: lowercase hex characters only

    Args:
        identity: The identity string (email, username, etc.)

    Returns:
        8-character hex prefix.
    """
    return hashlib.sha256(identity.encode()).hexdigest()[:8]


def get_developer_prefix(config: Optional[BootstrapConfig] = None) -> str:
    """Get the stable 8-character developer prefix.

    Priority order:
    1. Configured developer_prefix in config (if set)
    2. Auto-generated from git email
    3. Auto-generated from $USER

    Args:
        config: Optional BootstrapConfig to check for configured prefix.

    Returns:
        8-character developer prefix.
    """
    # Check config first
    if config is not None:
        prefix = getattr(config, "developer_prefix", "")
        if prefix:
            return prefix

    # Auto-generate from identity
    identity = get_developer_identity()
    return generate_developer_prefix(identity)


def get_aws_bucket_name(project: str, config: Optional[BootstrapConfig] = None) -> str:
    """Get the AWS bucket name with developer prefix.

    Format: {project}-{developer_prefix}-data

    Args:
        project: Project name (e.g., "cybersec")
        config: Optional BootstrapConfig for prefix lookup.

    Returns:
        Full bucket name with developer isolation.
    """
    prefix = get_developer_prefix(config)
    return f"{project}-{prefix}-data"


def get_developer_email(config: Optional[BootstrapConfig] = None) -> str:
    """Get the developer email for tagging.

    Priority order:
    1. Configured developer_email in config (if set)
    2. Git email
    3. "{USER}@local" fallback

    Args:
        config: Optional BootstrapConfig to check for configured email.

    Returns:
        Developer email string.
    """
    # Check config first
    if config is not None:
        email = getattr(config, "developer_email", "")
        if email:
            return email

    # Try git email
    git_email = get_git_email()
    if git_email:
        return git_email

    # Fallback
    user = os.environ.get("USER", "unknown")
    return f"{user}@local"
