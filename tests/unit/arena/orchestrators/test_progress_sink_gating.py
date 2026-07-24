from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

import pytest

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.application.runner_progress_mixin import (
    ENGINE_IO_QUEUE_BACKLOG_LIMIT,
)
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent


def _io_event() -> UsiIoEvent:
    return UsiIoEvent(direction="inbound", line="info depth 12 score cp 30 pv 7g7f", phase=None)


@pytest.mark.asyncio
async def test_engine_io_is_not_enqueued_without_a_consumer() -> None:
    runner = GameRunner(progress_queue=None)

    remove = runner._register_engine_io_listener(  # noqa: SLF001
        engine=object(),  # type: ignore[arg-type]
        role="black",
        game_id="g0001",
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    # No consumer means no handler registration at all, so engines never pay per-line dispatch.
    assert callable(remove)
    await runner._enqueue_engine_io_event(  # noqa: SLF001
        game_id="g0001",
        role="black",
        entry=_io_event(),
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )
    assert runner.progress_queue is None


@pytest.mark.asyncio
async def test_engine_io_handler_is_a_coroutine_function() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    registered: list[object] = []

    class _Engine:
        def register_io_log_handler(self, handler: object) -> Callable[[], None]:
            registered.append(handler)
            return lambda: None

    runner._register_engine_io_listener(  # noqa: SLF001
        engine=_Engine(),  # type: ignore[arg-type]
        role="black",
        game_id="g0001",
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    # A plain def handler would be dispatched through asyncio.to_thread once per USI line.
    assert len(registered) == 1
    assert inspect.iscoroutinefunction(registered[0])


@pytest.mark.asyncio
async def test_engine_io_is_enqueued_when_a_consumer_exists() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)

    await runner._enqueue_engine_io_event(  # noqa: SLF001
        game_id="g0001",
        role="black",
        entry=_io_event(),
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_engine_io_is_dropped_once_the_consumer_falls_behind() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    for _ in range(ENGINE_IO_QUEUE_BACKLOG_LIMIT):
        queue.put_nowait((1, 0, None))

    await runner._enqueue_engine_io_event(  # noqa: SLF001
        game_id="g0001",
        role="black",
        entry=_io_event(),
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    assert queue.qsize() == ENGINE_IO_QUEUE_BACKLOG_LIMIT
    assert runner._dropped_engine_io_events == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_move_progress_is_never_dropped_by_the_engine_io_limit() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    for _ in range(ENGINE_IO_QUEUE_BACKLOG_LIMIT):
        queue.put_nowait((1, 0, None))

    # State-bearing events must survive backlog; dropping them would corrupt the live game view.
    await runner._enqueue_progress(  # noqa: SLF001
        "g0001",
        1,
        {"type": "move", "game_id": "g0001"},  # type: ignore[typeddict-item]
    )

    assert queue.qsize() == ENGINE_IO_QUEUE_BACKLOG_LIMIT + 1
