"""K8s target preparation and configuration module."""

from .prepare import (
    detect_target,
    prepare_target,
    validate_target,
    K8sTarget,
)

__all__ = [
    "detect_target",
    "prepare_target",
    "validate_target",
    "K8sTarget",
]
