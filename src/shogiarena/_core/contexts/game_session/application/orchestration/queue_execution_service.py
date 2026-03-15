"""Priority pending-queue execution service for orchestrators."""

from __future__ import annotations

import asyncio
from typing import TypeVar

from shogiarena._core.contexts.game_session.ports.pending_runtime import (
    PendingQueueConsumeRequest,
    PendingQueueEnqueueRequest,
    PendingQueueState,
)

TItem = TypeVar("TItem")


class PendingQueueExecutionService:
    """Execute queued items with bounded concurrency and sentinel shutdown."""

    def create_state(self) -> PendingQueueState[TItem]:
        return PendingQueueState(queue=asyncio.PriorityQueue())

    async def enqueue_item(self, request: PendingQueueEnqueueRequest[TItem]) -> None:
        state = request.state
        state.counter += 1
        await state.queue.put((request.priority, state.counter, request.item))

    async def consume(self, request: PendingQueueConsumeRequest[TItem]) -> None:
        state = request.state
        queue = state.queue
        semaphore = asyncio.Semaphore(request.concurrency_limit)
        fatal_error: Exception | None = None

        async def worker() -> None:
            nonlocal fatal_error
            while True:
                _priority, _counter, item = await queue.get()
                if item is None:
                    queue.task_done()
                    break
                if fatal_error is not None:
                    queue.task_done()
                    continue
                if request.runtime.should_skip_pending_item(item):
                    queue.task_done()
                    continue

                await semaphore.acquire()
                task: asyncio.Task[None] = asyncio.create_task(request.runtime.run_pending_item(item))
                request.running_tasks.add(task)
                try:
                    await task
                except Exception as exc:
                    if fatal_error is None:
                        fatal_error = exc
                finally:
                    request.running_tasks.discard(task)
                    semaphore.release()
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(request.concurrency_limit)]
        request.worker_tasks.update(workers)

        await queue.join()
        for _ in workers:
            await self.enqueue_item(
                PendingQueueEnqueueRequest(
                    state=state,
                    priority=request.sentinel_priority,
                    item=None,
                )
            )

        await asyncio.gather(*workers)
        for worker_task in workers:
            request.worker_tasks.discard(worker_task)

        if fatal_error is not None:
            raise fatal_error


__all__ = [
    "PendingQueueExecutionService",
]
