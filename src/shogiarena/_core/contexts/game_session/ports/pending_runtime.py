"""Pending schedule/queue runtime contracts."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Collection
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


class PendingScheduleSpecPort(Protocol):
    """Minimal schedule item contract required by pending scheduling."""

    game_id: str


TPendingSpec = TypeVar("TPendingSpec", bound=PendingScheduleSpecPort)


@dataclass
class PendingScheduleState(Generic[TPendingSpec]):
    """Mutable pending schedule state owned by orchestrators."""

    schedule: list[TPendingSpec]
    display_order_by_game_id: dict[str, int]


@dataclass
class PendingRuntimeState(Generic[TPendingSpec]):
    """Unified mutable state for pending schedule + queue execution."""

    schedule: PendingScheduleState[TPendingSpec]
    queue: PendingQueueState[TPendingSpec] | None = None


@dataclass(frozen=True)
class PendingScheduleCollectRequest(Generic[TPendingSpec]):
    """Input contract for collecting pending schedule specs."""

    state: PendingScheduleState[TPendingSpec]
    completed_game_ids: Collection[str]
    cancelled_game_ids: Collection[str]


@dataclass(frozen=True)
class PendingScheduleDisplayOrderRequest(Generic[TPendingSpec]):
    """Input contract for display-order resolution."""

    state: PendingScheduleState[TPendingSpec]
    spec: TPendingSpec


@dataclass(frozen=True)
class PendingScheduleRestoreRequest(Generic[TPendingSpec]):
    """Input contract for merging a restored schedule item."""

    state: PendingScheduleState[TPendingSpec]
    spec: TPendingSpec
    display_order: int


TQueueItem = TypeVar("TQueueItem")


class PendingQueueExecutionRuntimePort(Protocol[TQueueItem]):
    """Runtime contract for pending queue item execution."""

    def should_skip_pending_item(self, item: TQueueItem) -> bool: ...

    async def run_pending_item(self, item: TQueueItem) -> None: ...


@dataclass
class PendingQueueState(Generic[TQueueItem]):
    """Mutable queue state owned by orchestrators."""

    queue: asyncio.PriorityQueue[tuple[int, int, TQueueItem | None]]
    counter: int = 0


@dataclass(frozen=True)
class PendingQueueEnqueueRequest(Generic[TQueueItem]):
    """Input contract for queue enqueue operations."""

    state: PendingQueueState[TQueueItem]
    priority: int
    item: TQueueItem | None


@dataclass(frozen=True)
class PendingQueueConsumeRequest(Generic[TQueueItem]):
    """Input contract for bounded queue consumption operations."""

    state: PendingQueueState[TQueueItem]
    concurrency_limit: int
    running_tasks: set[asyncio.Task[None]]
    worker_tasks: set[asyncio.Task[None]]
    runtime: PendingQueueExecutionRuntimePort[TQueueItem]
    sentinel_priority: int = sys.maxsize


__all__ = [
    "PendingQueueExecutionRuntimePort",
    "PendingQueueConsumeRequest",
    "PendingQueueEnqueueRequest",
    "PendingQueueState",
    "PendingRuntimeState",
    "PendingScheduleCollectRequest",
    "PendingScheduleDisplayOrderRequest",
    "PendingScheduleRestoreRequest",
    "PendingScheduleSpecPort",
    "PendingScheduleState",
]
