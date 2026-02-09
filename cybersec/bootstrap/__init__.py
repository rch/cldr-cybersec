"""Bootstrap system for cybersec devenv environment.

This module provides unified bootstrap functionality across:
- CLI (cybersec bootstrap ...)
- MCP Server (bootstrap_* tools)
- Web UI (/api/bootstrap/*, /settings)
"""

from .config import BootstrapConfig, SettingsManager
from .state import BootstrapState, TaskStatus, TaskResult
from .events import EventEmitter, EventType, BootstrapEvent
from .service import BootstrapService
from .submodules import (
    SubmoduleSpec,
    SUBMODULE_SPECS,
    is_submodule_initialized,
    get_submodule_branch,
    ensure_submodule_initialized,
    ensure_submodule_updated,
    prepare_submodule,
    prepare_all_submodules,
    get_submodule_status,
)

__all__ = [
    "BootstrapConfig",
    "SettingsManager",
    "BootstrapState",
    "TaskStatus",
    "TaskResult",
    "EventEmitter",
    "EventType",
    "BootstrapEvent",
    "BootstrapService",
    # Submodule management
    "SubmoduleSpec",
    "SUBMODULE_SPECS",
    "is_submodule_initialized",
    "get_submodule_branch",
    "ensure_submodule_initialized",
    "ensure_submodule_updated",
    "prepare_submodule",
    "prepare_all_submodules",
    "get_submodule_status",
]
