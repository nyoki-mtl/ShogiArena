"""Shared runtime helpers for BaseOrchestrator."""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Awaitable, Callable
from typing import TypeVar

_T = TypeVar("_T")


async def run_items_concurrently(
    *,
    stop_event: asyncio.Event,
    worker_tasks: set[asyncio.Task[None]],
    running_tasks: set[asyncio.Task[None]],
    items: list[_T],
    run_one: Callable[[_T], Awaitable[None]],
    concurrency_limit: int,
) -> None:
    """Run items concurrently with semaphore and stop support."""

    if not items:
        return
    if concurrency_limit < 1:
        raise ValueError(f"concurrency_limit must be >= 1, got {concurrency_limit}")
    semaphore = asyncio.Semaphore(concurrency_limit)
    lock = asyncio.Lock()
    idx = {"i": 0}

    async def next_item() -> _T | None:
        async with lock:
            if stop_event.is_set():
                return None
            if idx["i"] >= len(items):
                return None
            it = items[idx["i"]]
            idx["i"] += 1
            return it

    async def _invoke(x: _T) -> None:
        await run_one(x)

    async def worker() -> None:
        while not stop_event.is_set():
            it = await next_item()
            if it is None:
                break
            async with semaphore:
                task: asyncio.Task[None] = asyncio.create_task(_invoke(it))
                running_tasks.add(task)
                try:
                    await task
                except BaseException:
                    # Stop siblings from picking up new items so the whole fan-out winds down
                    # cleanly instead of leaving detached tasks running after the gather re-raises.
                    stop_event.set()
                    raise
                finally:
                    running_tasks.discard(task)

    workers = [asyncio.create_task(worker()) for _ in range(concurrency_limit)]
    worker_tasks.update(workers)
    try:
        results = await asyncio.gather(*workers, return_exceptions=True)
    finally:
        for worker_task in workers:
            worker_tasks.discard(worker_task)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def numeric_game_id(game_id: str | int) -> int:
    """Convert a game identifier to a stable non-negative integer."""

    if isinstance(game_id, int):
        return game_id
    if isinstance(game_id, str) and game_id.startswith("game_"):
        return int(game_id.split("_", 1)[1])
    return zlib.crc32(str(game_id).encode("utf-8")) & 0x7FFFFFFF


__all__ = ["numeric_game_id", "run_items_concurrently"]
