"""SPMC BroadcastQueue for engine event fan-out.

Follows the EventEmitter pattern from cybersec/bootstrap/events.py,
adapted for asyncio queues and protobuf EngineEvent messages.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import weakref

from cybersec.engine.generated import navigator_pb2 as pb

logger = logging.getLogger(__name__)

# Default history cap — matches EventEmitter._max_history
MAX_HISTORY = 1000


class BroadcastQueue:
    """Single-producer, multi-consumer event broadcast.

    Each call to subscribe() returns an asyncio.Queue that receives a copy of
    every event published after subscription.  Queues are weakly referenced and
    cleaned up automatically when the subscriber drops.
    """

    def __init__(self, max_history: int = MAX_HISTORY):
        self._subscribers: list[weakref.ref[asyncio.Queue[pb.EngineEvent]]] = []
        self._history: collections.deque[pb.EngineEvent] = collections.deque(maxlen=max_history)
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[pb.EngineEvent]:
        """Create a new subscription queue."""
        q: asyncio.Queue[pb.EngineEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(weakref.ref(q))
        return q

    def publish(self, event: pb.EngineEvent) -> None:
        """Publish an event to all active subscribers (non-blocking)."""
        self._history.append(event)
        alive: list[weakref.ref[asyncio.Queue[pb.EngineEvent]]] = []
        for ref in self._subscribers:
            q = ref()
            if q is None:
                continue
            alive.append(ref)
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full — dropping event %s", event.event_id)
        self._subscribers = alive

    def get_history(self, limit: int = 50) -> list[pb.EngineEvent]:
        """Return recent events for replay."""
        items = list(self._history)
        return items[-limit:] if limit else items

    @property
    def subscriber_count(self) -> int:
        return sum(1 for ref in self._subscribers if ref() is not None)
