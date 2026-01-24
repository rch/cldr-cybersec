"""Event system for bootstrap progress updates.

Provides a unified way to emit and consume bootstrap events across all interfaces:
- CLI: Updates progress bars, prints status
- Web: Sends SSE events to browser
- MCP: Includes in tool response
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Any, Optional
import asyncio
from collections import defaultdict
import json


class EventType(Enum):
    """Types of bootstrap events."""

    # Task lifecycle
    TASK_REGISTERED = "task_registered"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_SKIPPED = "task_skipped"
    TASK_WAITING_INPUT = "task_waiting_input"

    # Bootstrap lifecycle
    BOOTSTRAP_STARTED = "bootstrap_started"
    BOOTSTRAP_PHASE_CHANGED = "bootstrap_phase_changed"
    BOOTSTRAP_COMPLETED = "bootstrap_completed"
    BOOTSTRAP_FAILED = "bootstrap_failed"

    # Service events
    SERVICE_HEALTHY = "service_healthy"
    SERVICE_UNHEALTHY = "service_unhealthy"

    # User interaction
    PROMPT_REQUIRED = "prompt_required"
    PROMPT_RESPONSE = "prompt_response"

    # Log events
    LOG_INFO = "log_info"
    LOG_WARN = "log_warn"
    LOG_ERROR = "log_error"
    LOG_DEBUG = "log_debug"


@dataclass
class PromptOption:
    """An option for a user prompt."""

    key: str  # Short key for selection (e.g., "1", "a")
    label: str  # Display label
    description: str = ""
    default: bool = False


@dataclass
class BootstrapEvent:
    """Event emitted during bootstrap process."""

    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    task_id: Optional[str] = None
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    progress: Optional[float] = None  # 0.0 to 1.0

    # For PROMPT_REQUIRED events
    prompt_options: list[PromptOption] = field(default_factory=list)
    prompt_allow_custom: bool = False  # Allow free-text input

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "task_id": self.task_id,
            "message": self.message,
            "data": self.data,
            "progress": self.progress,
        }
        if self.prompt_options:
            result["prompt_options"] = [
                {"key": o.key, "label": o.label, "description": o.description, "default": o.default}
                for o in self.prompt_options
            ]
            result["prompt_allow_custom"] = self.prompt_allow_custom
        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"data: {self.to_json()}\n\n"


class EventEmitter:
    """Event emitter for bootstrap progress updates.

    Supports both synchronous and asynchronous listeners.
    """

    def __init__(self):
        self._listeners: dict[EventType, list[Callable]] = defaultdict(list)
        self._async_listeners: dict[EventType, list[Callable]] = defaultdict(list)
        self._all_listeners: list[Callable] = []
        self._async_all_listeners: list[Callable] = []
        self._event_history: list[BootstrapEvent] = []
        self._max_history: int = 1000

    def on(self, event_type: EventType, callback: Callable[[BootstrapEvent], None]):
        """Register a synchronous event listener for a specific event type."""
        self._listeners[event_type].append(callback)

    def on_async(self, event_type: EventType, callback: Callable[[BootstrapEvent], Any]):
        """Register an async event listener for a specific event type."""
        self._async_listeners[event_type].append(callback)

    def on_all(self, callback: Callable[[BootstrapEvent], None]):
        """Register a listener for all events."""
        self._all_listeners.append(callback)

    def on_all_async(self, callback: Callable[[BootstrapEvent], Any]):
        """Register an async listener for all events."""
        self._async_all_listeners.append(callback)

    def off(self, event_type: EventType, callback: Callable):
        """Remove a listener."""
        if callback in self._listeners[event_type]:
            self._listeners[event_type].remove(callback)
        if callback in self._async_listeners[event_type]:
            self._async_listeners[event_type].remove(callback)

    def off_all(self, callback: Callable):
        """Remove a listener from all events."""
        if callback in self._all_listeners:
            self._all_listeners.remove(callback)
        if callback in self._async_all_listeners:
            self._async_all_listeners.remove(callback)

    def emit(self, event: BootstrapEvent):
        """Emit event to synchronous listeners only."""
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history :]

        for callback in self._listeners[event.event_type]:
            try:
                callback(event)
            except Exception:
                pass  # Don't let listener errors break emission

        for callback in self._all_listeners:
            try:
                callback(event)
            except Exception:
                pass

    async def emit_async(self, event: BootstrapEvent):
        """Emit event to both sync and async listeners."""
        # First emit to sync listeners
        self.emit(event)

        # Then emit to async listeners
        for callback in self._async_listeners[event.event_type]:
            try:
                await callback(event)
            except Exception:
                pass

        for callback in self._async_all_listeners:
            try:
                await callback(event)
            except Exception:
                pass

    def get_history(self, since: Optional[datetime] = None) -> list[BootstrapEvent]:
        """Get event history, optionally filtered by time."""
        if since is None:
            return list(self._event_history)
        return [e for e in self._event_history if e.timestamp >= since]

    def clear_history(self):
        """Clear event history."""
        self._event_history.clear()

    # Convenience methods for creating and emitting events

    def log_info(self, message: str, task_id: Optional[str] = None, **data):
        """Emit an info log event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.LOG_INFO,
                task_id=task_id,
                message=message,
                data=data,
            )
        )

    def log_warn(self, message: str, task_id: Optional[str] = None, **data):
        """Emit a warning log event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.LOG_WARN,
                task_id=task_id,
                message=message,
                data=data,
            )
        )

    def log_error(self, message: str, task_id: Optional[str] = None, **data):
        """Emit an error log event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.LOG_ERROR,
                task_id=task_id,
                message=message,
                data=data,
            )
        )

    def task_started(self, task_id: str, message: str = ""):
        """Emit task started event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.TASK_STARTED,
                task_id=task_id,
                message=message,
            )
        )

    def task_progress(self, task_id: str, progress: float, message: str = ""):
        """Emit task progress event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.TASK_PROGRESS,
                task_id=task_id,
                message=message,
                progress=progress,
            )
        )

    def task_completed(self, task_id: str, message: str = "", **data):
        """Emit task completed event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.TASK_COMPLETED,
                task_id=task_id,
                message=message,
                data=data,
                progress=1.0,
            )
        )

    def task_failed(self, task_id: str, error: str, **data):
        """Emit task failed event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.TASK_FAILED,
                task_id=task_id,
                message=error,
                data=data,
            )
        )

    def phase_changed(self, phase: str, message: str = ""):
        """Emit phase changed event."""
        self.emit(
            BootstrapEvent(
                event_type=EventType.BOOTSTRAP_PHASE_CHANGED,
                message=message,
                data={"phase": phase},
            )
        )

    def prompt_required(
        self,
        task_id: str,
        message: str,
        options: list[PromptOption],
        allow_custom: bool = False,
    ) -> BootstrapEvent:
        """Emit a prompt required event and return it."""
        event = BootstrapEvent(
            event_type=EventType.PROMPT_REQUIRED,
            task_id=task_id,
            message=message,
            prompt_options=options,
            prompt_allow_custom=allow_custom,
        )
        self.emit(event)
        return event


class EventCollector:
    """Collects events into a list for later processing.

    Useful for CLI/MCP where we want to collect all events and return them.
    """

    def __init__(self):
        self.events: list[BootstrapEvent] = []

    def collect(self, event: BootstrapEvent):
        """Add event to collection."""
        self.events.append(event)

    def get_messages(self) -> list[str]:
        """Get all messages from collected events."""
        return [e.message for e in self.events if e.message]

    def get_errors(self) -> list[str]:
        """Get error messages from collected events."""
        return [
            e.message
            for e in self.events
            if e.event_type in (EventType.TASK_FAILED, EventType.LOG_ERROR, EventType.BOOTSTRAP_FAILED)
        ]

    def to_summary(self) -> str:
        """Generate a summary of collected events."""
        lines = []
        for event in self.events:
            prefix = {
                EventType.TASK_STARTED: "[~]",
                EventType.TASK_COMPLETED: "[+]",
                EventType.TASK_FAILED: "[X]",
                EventType.TASK_SKIPPED: "[-]",
                EventType.LOG_INFO: "[i]",
                EventType.LOG_WARN: "[!]",
                EventType.LOG_ERROR: "[X]",
                EventType.BOOTSTRAP_COMPLETED: "[+]",
                EventType.BOOTSTRAP_FAILED: "[X]",
            }.get(event.event_type, "   ")

            if event.message:
                lines.append(f"{prefix} {event.message}")

        return "\n".join(lines)

    def clear(self):
        """Clear collected events."""
        self.events.clear()
