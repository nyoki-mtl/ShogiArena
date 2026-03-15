"""Runtime state models for the dashboard WebSocket hub."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from aiohttp import web


@dataclass
class TopicState:
    seq: int = 0
    ring: deque[str] = field(default_factory=deque)
    last_published_ms: int = 0


@dataclass(eq=False)
class LiveWsClient:
    ws: web.WebSocketResponse
    worker_filter: set[int] | None
    client_id: int = 0
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(maxsize=1024))
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    subscriptions: set[str] | None = None
    should_include_analysis: bool = True

    def __hash__(self) -> int:  # pragma: no cover - trivial
        return id(self)


__all__ = [
    "LiveWsClient",
    "TopicState",
]
