"""Viewport bounds management with debounced recalculation.

This module provides the BoundsManager class for handling interactive
zoom/pan operations with efficient debouncing to avoid redundant renders.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import param

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class ViewportBounds:
    """Bounding box for the current viewport.

    Attributes:
        x_min: Minimum X value (typically timestamp)
        x_max: Maximum X value (typically timestamp)
        y_min: Minimum Y value (typically duration/latency)
        y_max: Maximum Y value (typically duration/latency)
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def x_range(self) -> tuple[float, float]:
        """X range as tuple."""
        return (self.x_min, self.x_max)

    @property
    def y_range(self) -> tuple[float, float]:
        """Y range as tuple."""
        return (self.y_min, self.y_max)

    @property
    def x_span(self) -> float:
        """Width of X range."""
        return self.x_max - self.x_min

    @property
    def y_span(self) -> float:
        """Height of Y range."""
        return self.y_max - self.y_min

    def scale(self, fraction: float) -> "ViewportBounds":
        """Create zoomed bounds centered on midpoint.

        Args:
            fraction: Fraction of original range (e.g., 0.1 for 10%)

        Returns:
            New ViewportBounds with scaled range
        """
        x_mid = (self.x_min + self.x_max) / 2
        y_mid = (self.y_min + self.y_max) / 2
        x_half = self.x_span * fraction / 2
        y_half = self.y_span * fraction / 2

        return ViewportBounds(
            x_min=x_mid - x_half,
            x_max=x_mid + x_half,
            y_min=y_mid - y_half,
            y_max=y_mid + y_half,
        )

    def delta_from(self, other: "ViewportBounds") -> tuple[float, float]:
        """Calculate relative change from another bounds.

        Args:
            other: Previous bounds to compare against

        Returns:
            Tuple of (x_delta_pct, y_delta_pct) as fractions
        """
        if other.x_span == 0 or other.y_span == 0:
            return (1.0, 1.0)

        x_delta = abs(self.x_min - other.x_min) + abs(self.x_max - other.x_max)
        y_delta = abs(self.y_min - other.y_min) + abs(self.y_max - other.y_max)

        return (x_delta / other.x_span, y_delta / other.y_span)


class BoundsManager(param.Parameterized):
    """Manages viewport bounds with debounced updates.

    This class handles zoom/pan events with debouncing to avoid
    triggering excessive re-renders. Updates are coalesced within
    the debounce window and only trigger a callback when:
    1. The debounce delay has passed since the last change
    2. The change exceeds the minimum delta threshold

    Example:
        manager = BoundsManager(
            debounce_ms=100,
            min_delta_pct=0.02,
            on_bounds_change=lambda b: render(b),
        )
        manager.update_bounds(x_range=(0, 100), y_range=(0, 50))
    """

    # Current viewport bounds as param.Range for Panel binding
    x_range = param.Range(default=(0.0, 1.0), doc="Current X axis range")
    y_range = param.Range(default=(0.0, 1.0), doc="Current Y axis range")

    # State indicators
    render_pending = param.Boolean(default=False, doc="Whether a render is pending")
    last_render_time_ms = param.Number(default=0.0, doc="Time of last render in ms")

    def __init__(
        self,
        debounce_ms: int = 100,
        min_delta_pct: float = 0.02,
        on_bounds_change: Callable[[ViewportBounds], None] | None = None,
        **params,
    ):
        """Initialize BoundsManager.

        Args:
            debounce_ms: Debounce delay in milliseconds
            min_delta_pct: Minimum viewport change to trigger update (0.02 = 2%)
            on_bounds_change: Callback invoked when bounds should be updated
        """
        super().__init__(**params)
        self._debounce_ms = debounce_ms
        self._min_delta_pct = min_delta_pct
        self._on_bounds_change = on_bounds_change
        self._debounce_task: asyncio.Task | None = None
        self._last_bounds: ViewportBounds | None = None
        self._pending_bounds: ViewportBounds | None = None

    @property
    def current_bounds(self) -> ViewportBounds:
        """Get current viewport bounds."""
        return ViewportBounds(
            x_min=self.x_range[0],
            x_max=self.x_range[1],
            y_min=self.y_range[0],
            y_max=self.y_range[1],
        )

    def set_initial_bounds(self, bounds: ViewportBounds) -> None:
        """Set initial bounds without triggering callback.

        Args:
            bounds: Initial viewport bounds
        """
        self.x_range = bounds.x_range
        self.y_range = bounds.y_range
        self._last_bounds = bounds

    def update_bounds(
        self,
        x_range: tuple[float, float] | None = None,
        y_range: tuple[float, float] | None = None,
    ) -> None:
        """Update viewport bounds with debouncing.

        This method schedules a bounds update with debouncing. Multiple
        rapid calls will be coalesced, and the callback will only be
        invoked after the debounce delay with the final bounds.

        Args:
            x_range: New X range (min, max), or None to keep current
            y_range: New Y range (min, max), or None to keep current
        """
        if x_range is not None:
            self.x_range = x_range
        if y_range is not None:
            self.y_range = y_range

        new_bounds = self.current_bounds
        self._pending_bounds = new_bounds
        self.render_pending = True

        # Schedule debounced update
        self._schedule_update()

    def _schedule_update(self) -> None:
        """Schedule a debounced update task."""
        # Cancel any existing pending task
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()

        # Create new debounce task
        try:
            loop = asyncio.get_running_loop()
            self._debounce_task = loop.create_task(self._debounced_update())
        except RuntimeError:
            # No running event loop - execute synchronously for testing
            self._execute_update()

    async def _debounced_update(self) -> None:
        """Wait for debounce delay then execute update."""
        try:
            await asyncio.sleep(self._debounce_ms / 1000.0)
            self._execute_update()
        except asyncio.CancelledError:
            # Debounce was reset by a newer update
            pass

    def _execute_update(self) -> None:
        """Execute the bounds update if threshold is met."""
        if self._pending_bounds is None:
            self.render_pending = False
            return

        bounds = self._pending_bounds
        self._pending_bounds = None

        # Check if change exceeds minimum threshold
        if self._last_bounds is not None:
            x_delta, y_delta = bounds.delta_from(self._last_bounds)
            if max(x_delta, y_delta) < self._min_delta_pct:
                logger.debug(
                    f"Skipping update: delta ({x_delta:.3f}, {y_delta:.3f}) "
                    f"< threshold ({self._min_delta_pct})"
                )
                self.render_pending = False
                return

        # Record update time
        start_time = time.perf_counter()

        # Invoke callback
        if self._on_bounds_change is not None:
            try:
                self._on_bounds_change(bounds)
            except Exception as e:
                logger.error(f"Error in bounds change callback: {e}")
                raise

        # Update state
        self._last_bounds = bounds
        self.render_pending = False
        self.last_render_time_ms = (time.perf_counter() - start_time) * 1000

        logger.debug(
            f"Bounds updated: x={bounds.x_range}, y={bounds.y_range}, "
            f"render_time={self.last_render_time_ms:.1f}ms"
        )

    @param.depends("x_range", "y_range", watch=True)
    def _on_range_change(self) -> None:
        """Handle param range changes from Panel widgets."""
        # This is triggered by external Panel widget updates
        # Schedule a debounced update
        self._schedule_update()
