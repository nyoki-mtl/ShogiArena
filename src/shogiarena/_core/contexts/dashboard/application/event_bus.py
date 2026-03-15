"""In-process event bus utilities for dashboard interface coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Generic, TypeVar

_E = TypeVar("_E")


@dataclass(frozen=True)
class Event(Generic[_E]):
    """Generic event wrapper published to the local event bus."""

    type: str
    payload: _E


class EventBus(Generic[_E]):
    """Simple in-process event bus with type-based fan-out."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event[_E]], None]]] = {}
        self._lock = RLock()

    def subscribe(self, event_type: str, handler: Callable[[Event[_E]], None]) -> Callable[[], None]:
        """Register a handler and return a detach callback."""
        with self._lock:
            handlers = self._handlers.setdefault(event_type, [])
            handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                bucket = self._handlers.get(event_type)
                if bucket is None:
                    return
                try:
                    bucket.remove(handler)
                except ValueError:
                    return
                if not bucket:
                    self._handlers.pop(event_type, None)

        return unsubscribe

    def publish(self, event: Event[_E]) -> None:
        """Publish a single event to all registered handlers."""
        with self._lock:
            handlers = list(self._handlers.get(event.type, ()))

        for handler in handlers:
            handler(event)


__all__ = ["Event", "EventBus"]
