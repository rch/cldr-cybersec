"""Automatic submodule management - no manual commands needed.

This module handles git submodule initialization and updates automatically
during bootstrap. Developers never need to run manual submodule commands.
"""
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import logging

logger = logging.getLogger(__name__)


@dataclass
class SubmoduleSpec:
    """Expected submodule configuration."""

    path: str
    expected_branch: str
    required_for: list[str] = field(default_factory=list)
    # Marker files that indicate the submodule is initialized
    marker_files: list[str] = field(
        default_factory=lambda: [".git", "pom.xml", "build.gradle", "build.gradle.kts", "setup.py", "pyproject.toml"]
    )


# Expected submodule versions - update when requirements change
SUBMODULE_SPECS: dict[str, SubmoduleSpec] = {
    "flink": SubmoduleSpec(
        path="thirdparty/flink",
        expected_branch="rch/devenv-cybersec",
        required_for=["flink", "pyflink"],
        marker_files=["pom.xml"],
    ),
    "polaris": SubmoduleSpec(
        path="thirdparty/polaris",
        expected_branch="main",
        required_for=["polaris", "iceberg"],
        marker_files=["build.gradle.kts", "settings.gradle.kts"],
    ),
    "iceberg": SubmoduleSpec(
        path="thirdparty/iceberg",
        expected_branch="main",
        required_for=["iceberg-connectors"],
        marker_files=["build.gradle", "settings.gradle"],
    ),
}


def is_submodule_initialized(name: str) -> bool:
    """Check if a submodule is initialized (has content).

    Args:
        name: Submodule name (flink, polaris, iceberg)

    Returns:
        True if submodule directory has expected marker files
    """
    spec = SUBMODULE_SPECS.get(name)
    if not spec:
        return True  # Unknown submodule is considered "ok"

    path = Path(spec.path)
    if not path.exists():
        return False

    # Check for any marker file
    return any((path / f).exists() for f in spec.marker_files)


def get_submodule_branch(name: str) -> str | None:
    """Get the current branch of a submodule.

    Args:
        name: Submodule name

    Returns:
        Branch name, "HEAD detached", or None if not a git repo
    """
    spec = SUBMODULE_SPECS.get(name)
    if not spec:
        return None

    path = Path(spec.path)
    if not path.exists():
        return None

    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    branch = result.stdout.strip()
    return branch if branch else None


def ensure_submodule_initialized(name: str) -> bool:
    """Automatically initialize submodule if needed.

    Called during bootstrap - runs `git submodule update --init` automatically.

    Args:
        name: Submodule name (flink, polaris, iceberg)

    Returns:
        True if submodule is ready, False if initialization failed
    """
    spec = SUBMODULE_SPECS.get(name)
    if not spec:
        return True  # Unknown submodule considered ok

    if is_submodule_initialized(name):
        return True  # Already initialized

    # Auto-initialize
    logger.info(f"Initializing {name} submodule...")
    result = subprocess.run(
        ["git", "submodule", "update", "--init", spec.path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(f"Failed to initialize {name}: {result.stderr}")
        return False

    return is_submodule_initialized(name)


def ensure_submodule_updated(name: str, auto_checkout: bool = False) -> bool:
    """Check if submodule is on expected branch.

    By default, this only checks the branch - it doesn't force checkout.
    Set auto_checkout=True to automatically switch branches.

    Args:
        name: Submodule name
        auto_checkout: If True, checkout expected branch automatically

    Returns:
        True if submodule is on correct branch (or update succeeded)
    """
    spec = SUBMODULE_SPECS.get(name)
    if not spec:
        return True

    current_branch = get_submodule_branch(name)
    if current_branch is None:
        return False

    # HEAD detached is common for submodules, don't force branch checkout
    if current_branch == "HEAD":
        logger.debug(f"{name} submodule is in detached HEAD state (normal for submodules)")
        return True

    if current_branch == spec.expected_branch:
        return True

    if not auto_checkout:
        logger.info(f"{name} submodule on branch '{current_branch}' (expected: {spec.expected_branch})")
        return True  # Still usable, just on different branch

    # Auto-checkout requested
    path = Path(spec.path)
    logger.info(f"Updating {name} to branch {spec.expected_branch}...")

    # Fetch first
    subprocess.run(["git", "-C", str(path), "fetch", "origin"], capture_output=True)

    # Checkout expected branch
    result = subprocess.run(
        ["git", "-C", str(path), "checkout", spec.expected_branch],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.warning(f"Could not checkout {spec.expected_branch}: {result.stderr}")
        return False

    # Pull latest
    subprocess.run(["git", "-C", str(path), "pull"], capture_output=True)

    return True


def prepare_submodule(name: str, auto_checkout: bool = False) -> tuple[bool, str]:
    """Full submodule preparation: init + optional update.

    This is the main entry point for submodule handling.

    Args:
        name: Submodule name (flink, polaris, iceberg)
        auto_checkout: If True, checkout expected branch automatically

    Returns:
        Tuple of (success, status_message)
    """
    spec = SUBMODULE_SPECS.get(name)
    if not spec:
        return True, f"Unknown submodule '{name}' - skipped"

    # Step 1: Initialize if needed
    if not ensure_submodule_initialized(name):
        return False, f"Failed to initialize {name} submodule"

    # Step 2: Check/update branch
    if not ensure_submodule_updated(name, auto_checkout=auto_checkout):
        return False, f"Failed to update {name} submodule"

    # Get current state for message
    current_branch = get_submodule_branch(name)
    if current_branch == "HEAD":
        return True, f"{name} submodule ready (detached HEAD)"
    elif current_branch == spec.expected_branch:
        return True, f"{name} submodule ready (branch: {current_branch})"
    else:
        return True, f"{name} submodule ready (branch: {current_branch}, expected: {spec.expected_branch})"


def prepare_all_submodules(auto_checkout: bool = False) -> dict[str, tuple[bool, str]]:
    """Prepare all known submodules.

    Args:
        auto_checkout: If True, checkout expected branches automatically

    Returns:
        Dict mapping submodule name to (success, message) tuple
    """
    results = {}
    for name in SUBMODULE_SPECS:
        results[name] = prepare_submodule(name, auto_checkout=auto_checkout)
    return results


def get_submodule_status() -> dict[str, dict]:
    """Get status of all submodules.

    Returns:
        Dict with submodule status information
    """
    status = {}
    for name, spec in SUBMODULE_SPECS.items():
        initialized = is_submodule_initialized(name)
        branch = get_submodule_branch(name) if initialized else None
        on_expected = branch == spec.expected_branch if branch else False

        status[name] = {
            "path": spec.path,
            "initialized": initialized,
            "branch": branch,
            "expected_branch": spec.expected_branch,
            "on_expected_branch": on_expected,
            "required_for": spec.required_for,
        }
    return status
