"""Zarf air-gap and local deployment support.

This module provides:
- Preflight validation for air-gap deployment requirements
- Local deployment configuration gathering and validation
- Package building utilities
- Deployment helpers for RKE2 clusters
"""

from .preflight import (
    ZarfPreflightResult,
    run_preflight_checks,
)
from .local import gather_local_zarf_config

__all__ = [
    "ZarfPreflightResult",
    "run_preflight_checks",
    "gather_local_zarf_config",
]
