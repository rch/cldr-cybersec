"""Zarf air-gap deployment support.

This module provides:
- Preflight validation for air-gap deployment requirements
- Package building utilities
- Deployment helpers for RKE2 clusters
"""

from .preflight import (
    ZarfPreflightResult,
    run_preflight_checks,
)

__all__ = [
    "ZarfPreflightResult",
    "run_preflight_checks",
]
