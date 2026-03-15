"""Pending runtime orchestration service for schedule + queue coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.application.orchestration.queue_execution_service import (
    PendingQueueExecutionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.schedule_service import (
    PendingScheduleService,
)
from shogiarena._core.contexts.game_session.ports.pending_runtime import (
    PendingQueueConsumeRequest,
    PendingQueueEnqueueRequest,
    PendingQueueExecutionRuntimePort,
    PendingRuntimeState,
    PendingScheduleCollectRequest,
    PendingScheduleDisplayOrderRequest,
    PendingScheduleRestoreRequest,
    PendingScheduleSpecPort,
)

TPendingSpec = TypeVar("TPendingSpec", bound=PendingScheduleSpecPort)


class PendingRuntimeOrchestrationService(Generic[TPendingSpec]):
    """Coordinate pending schedule state and queue execution in one place."""

    def __init__(
        self,
        *,
        queue_service: PendingQueueExecutionService | None = None,
        schedule_service: PendingScheduleService[TPendingSpec] | None = None,
    ) -> None:
        self._queue_service = queue_service or PendingQueueExecutionService()
        self._schedule_service = schedule_service or PendingScheduleService()

    def initialize_state(self, schedule: Sequence[TPendingSpec]) -> PendingRuntimeState[TPendingSpec]:
        return PendingRuntimeState(schedule=self._schedule_service.initialize_state(schedule))

    def reset_schedule(
        self,
        state: PendingRuntimeState[TPendingSpec],
        *,
        schedule: Sequence[TPendingSpec],
    ) -> None:
        state.schedule = self._schedule_service.initialize_state(schedule)
        state.queue = None

    def collect_pending_specs(
        self,
        *,
        state: PendingRuntimeState[TPendingSpec],
        completed_game_ids: Collection[str],
        cancelled_game_ids: Collection[str],
    ) -> list[TPendingSpec]:
        return self._schedule_service.collect_pending_specs(
            PendingScheduleCollectRequest(
                state=state.schedule,
                completed_game_ids=completed_game_ids,
                cancelled_game_ids=cancelled_game_ids,
            )
        )

    def create_queue_state(self, *, state: PendingRuntimeState[TPendingSpec]) -> None:
        state.queue = self._queue_service.create_state()

    def clear_queue_state(self, *, state: PendingRuntimeState[TPendingSpec]) -> None:
        state.queue = None

    async def enqueue_pending_item(
        self,
        *,
        state: PendingRuntimeState[TPendingSpec],
        spec: TPendingSpec,
        display_order: int | None = None,
    ) -> None:
        pending_queue_state = state.queue
        if pending_queue_state is None:
            return
        if display_order is None:
            display_order = self._schedule_service.resolve_display_order(
                PendingScheduleDisplayOrderRequest(
                    state=state.schedule,
                    spec=spec,
                )
            )
        await self._queue_service.enqueue_item(
            PendingQueueEnqueueRequest(
                state=pending_queue_state,
                priority=display_order,
                item=spec,
            )
        )

    async def consume_pending_items(
        self,
        *,
        state: PendingRuntimeState[TPendingSpec],
        concurrency_limit: int,
        running_tasks: set[asyncio.Task[None]],
        worker_tasks: set[asyncio.Task[None]],
        runtime: PendingQueueExecutionRuntimePort[TPendingSpec],
    ) -> None:
        pending_queue_state = state.queue
        if pending_queue_state is None:
            return

        await self._queue_service.consume(
            PendingQueueConsumeRequest(
                state=pending_queue_state,
                concurrency_limit=concurrency_limit,
                running_tasks=running_tasks,
                worker_tasks=worker_tasks,
                runtime=runtime,
            )
        )

    async def execute_pending_items(
        self,
        *,
        state: PendingRuntimeState[TPendingSpec],
        pending_specs: Sequence[TPendingSpec],
        concurrency_limit: int,
        running_tasks: set[asyncio.Task[None]],
        worker_tasks: set[asyncio.Task[None]],
        runtime: PendingQueueExecutionRuntimePort[TPendingSpec],
    ) -> None:
        self.create_queue_state(state=state)
        try:
            for spec in pending_specs:
                await self.enqueue_pending_item(state=state, spec=spec)
            await self.consume_pending_items(
                state=state,
                concurrency_limit=concurrency_limit,
                running_tasks=running_tasks,
                worker_tasks=worker_tasks,
                runtime=runtime,
            )
        finally:
            self.clear_queue_state(state=state)

    async def enqueue_restored_item(
        self,
        *,
        state: PendingRuntimeState[TPendingSpec],
        spec: TPendingSpec,
        display_order: int,
    ) -> None:
        self._schedule_service.merge_restored_spec(
            PendingScheduleRestoreRequest(
                state=state.schedule,
                spec=spec,
                display_order=display_order,
            )
        )
        await self.enqueue_pending_item(
            state=state,
            spec=spec,
            display_order=display_order,
        )


__all__ = ["PendingRuntimeOrchestrationService"]
