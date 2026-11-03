"""Await owned persistence without abandoning it on caller cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_T = TypeVar("_T")


async def complete_before_cancelling(operation: Awaitable[_T]) -> _T:
    """Drain the operation, including lock waits, before propagating cancellation.

    Shield alone leaves the operation running after its owner exits. Repeated
    cancellation must not release a persistence lock or close a store early.
    Operation failures take precedence over a pending caller cancellation.
    Preserve the first caller cancellation, including its message. Cancellation
    counts belong to the caller's timeout/TaskGroup scope and are not cleared here.
    """
    task = asyncio.ensure_future(operation)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            if cancellation is None:
                cancellation = exc
    if cancellation is not None:
        raise cancellation
    return result
