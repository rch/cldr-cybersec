"""Bootstrap state management.

Tracks progress of bootstrap tasks and overall bootstrap state.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import json


class TaskStatus(Enum):
    """Status of a bootstrap task."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_INPUT = "waiting_input"  # Waiting for user input


class BootstrapPhase(Enum):
    """Current phase of the bootstrap process."""

    IDLE = "idle"
    ENVIRONMENT_CHECK = "environment_check"
    PORT_CHECK = "port_check"
    DIRECTORY_SETUP = "directory_setup"
    GIT_SUBMODULES = "git_submodules"
    FLINK_SETUP = "flink_setup"
    FLINK_CONNECTORS = "flink_connectors"
    POLARIS_SETUP = "polaris_setup"
    POLARIS_BINARIES = "polaris_binaries"
    SERVICE_HEALTH = "service_health"
    POLARIS_BOOTSTRAP = "polaris_bootstrap"
    CATALOG_INIT = "catalog_init"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class TaskResult:
    """Result of a single bootstrap task."""

    task_id: str
    status: TaskStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    progress: float = 0.0  # 0.0 to 1.0 for task-level progress

    @property
    def duration_seconds(self) -> Optional[float]:
        """Get duration of task execution."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def is_complete(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.SKIPPED)

    @property
    def is_success(self) -> bool:
        """Check if task succeeded."""
        return self.status == TaskStatus.SUCCESS

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
            "details": self.details,
            "error": self.error,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskResult":
        """Create from dictionary."""
        started = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
        completed = datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
        return cls(
            task_id=data["task_id"],
            status=TaskStatus(data["status"]),
            started_at=started,
            completed_at=completed,
            message=data.get("message", ""),
            details=data.get("details", {}),
            error=data.get("error"),
            progress=data.get("progress", 0.0),
        )


@dataclass
class BootstrapState:
    """Complete bootstrap state with task history."""

    phase: BootstrapPhase = BootstrapPhase.IDLE
    tasks: dict[str, TaskResult] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    # Service health cache (updated during service_health phase)
    service_health: dict[str, bool] = field(default_factory=dict)

    @property
    def overall_progress(self) -> float:
        """Calculate overall progress based on task statuses."""
        if not self.tasks:
            return 0.0

        completed = sum(1 for t in self.tasks.values() if t.is_complete)
        return completed / len(self.tasks)

    @property
    def is_complete(self) -> bool:
        """Check if bootstrap is complete (success or failure)."""
        return self.phase in (BootstrapPhase.COMPLETE, BootstrapPhase.ERROR)

    @property
    def is_success(self) -> bool:
        """Check if bootstrap completed successfully."""
        return self.phase == BootstrapPhase.COMPLETE

    @property
    def current_task(self) -> Optional[TaskResult]:
        """Get the currently running task, if any."""
        for task in self.tasks.values():
            if task.status == TaskStatus.RUNNING:
                return task
        return None

    @property
    def failed_tasks(self) -> list[TaskResult]:
        """Get all failed tasks."""
        return [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]

    def start(self):
        """Mark bootstrap as started."""
        self.started_at = datetime.now()
        self.phase = BootstrapPhase.ENVIRONMENT_CHECK

    def complete(self, success: bool = True, error: Optional[str] = None):
        """Mark bootstrap as complete."""
        self.completed_at = datetime.now()
        if success:
            self.phase = BootstrapPhase.COMPLETE
        else:
            self.phase = BootstrapPhase.ERROR
            self.error_message = error

    def register_task(self, task_id: str, description: str = ""):
        """Register a task for tracking."""
        self.tasks[task_id] = TaskResult(
            task_id=task_id,
            status=TaskStatus.PENDING,
            message=description,
        )

    def start_task(self, task_id: str, message: str = ""):
        """Mark a task as started."""
        if task_id not in self.tasks:
            self.register_task(task_id)

        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        if message:
            task.message = message

    def update_task_progress(self, task_id: str, progress: float, message: str = ""):
        """Update task progress."""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.progress = min(1.0, max(0.0, progress))
            if message:
                task.message = message

    def complete_task(
        self,
        task_id: str,
        success: bool = True,
        message: str = "",
        details: Optional[dict] = None,
        error: Optional[str] = None,
    ):
        """Mark a task as complete."""
        if task_id not in self.tasks:
            self.register_task(task_id)

        task = self.tasks[task_id]
        task.status = TaskStatus.SUCCESS if success else TaskStatus.FAILED
        task.completed_at = datetime.now()
        task.progress = 1.0
        if message:
            task.message = message
        if details:
            task.details = details
        if error:
            task.error = error

    def skip_task(self, task_id: str, reason: str = ""):
        """Mark a task as skipped."""
        if task_id not in self.tasks:
            self.register_task(task_id)

        task = self.tasks[task_id]
        task.status = TaskStatus.SKIPPED
        task.completed_at = datetime.now()
        task.message = reason or "Skipped"

    def set_waiting_input(self, task_id: str, message: str = ""):
        """Mark a task as waiting for user input."""
        if task_id not in self.tasks:
            self.register_task(task_id)

        task = self.tasks[task_id]
        task.status = TaskStatus.WAITING_INPUT
        if message:
            task.message = message

    def update_service_health(self, service_name: str, is_healthy: bool):
        """Update service health status."""
        self.service_health[service_name] = is_healthy

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "phase": self.phase.value,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "overall_progress": self.overall_progress,
            "service_health": self.service_health,
            "error_message": self.error_message,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "BootstrapState":
        """Create from dictionary."""
        state = cls()
        state.phase = BootstrapPhase(data.get("phase", "idle"))
        state.tasks = {k: TaskResult.from_dict(v) for k, v in data.get("tasks", {}).items()}
        state.started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
        state.completed_at = datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
        state.service_health = data.get("service_health", {})
        state.error_message = data.get("error_message")
        return state

    def get_summary(self) -> str:
        """Get a human-readable summary of the bootstrap state."""
        lines = []
        lines.append(f"Bootstrap Status: {self.phase.value}")
        lines.append(f"Progress: {self.overall_progress * 100:.0f}%")

        if self.tasks:
            lines.append("\nTasks:")
            for task in self.tasks.values():
                status_icon = {
                    TaskStatus.PENDING: "[ ]",
                    TaskStatus.RUNNING: "[~]",
                    TaskStatus.SUCCESS: "[+]",
                    TaskStatus.FAILED: "[X]",
                    TaskStatus.SKIPPED: "[-]",
                    TaskStatus.WAITING_INPUT: "[?]",
                }.get(task.status, "[?]")
                lines.append(f"  {status_icon} {task.task_id}: {task.message}")
                if task.error:
                    lines.append(f"      Error: {task.error}")

        if self.service_health:
            lines.append("\nService Health:")
            for service, healthy in self.service_health.items():
                icon = "+" if healthy else "X"
                lines.append(f"  [{icon}] {service}")

        return "\n".join(lines)
