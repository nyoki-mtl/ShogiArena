"""Regression tests for orchestration concurrency / aggregation helpers."""

from __future__ import annotations

import asyncio

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.concurrent_executor import (
    run_items_concurrently,
)
from shogiarena._core.contexts.game_session.application.orchestration.remote_move_aggregation import (
    update_remote_move_aggregates,
)


def _aggregate(events: list[dict]) -> tuple[list[str], int]:
    moves: list[str] = []
    buffers: dict[str, list] = {k: [] for k in ("evals", "nodes", "depth", "seldepth", "move_times", "wall_times")}
    seen = 0
    for event in events:
        seen = update_remote_move_aggregates(
            event,
            last_ply_seen=seen,
            moves=moves,
            evals=buffers["evals"],
            nodes=buffers["nodes"],
            depth=buffers["depth"],
            seldepth=buffers["seldepth"],
            move_times=buffers["move_times"],
            wall_times=buffers["wall_times"],
        )
    return moves, seen


def test_remote_move_aggregation_appends_in_order() -> None:
    moves, seen = _aggregate(
        [
            {"type": "move_progress", "ply": 1, "move": "7g7f"},
            {"type": "move_progress", "ply": 2, "move": "3c3d"},
        ]
    )
    assert moves == ["7g7f", "3c3d"]
    assert seen == 2


def test_remote_move_aggregation_skips_duplicate_and_out_of_order_plies() -> None:
    moves, seen = _aggregate(
        [
            {"type": "move_progress", "ply": 1, "move": "7g7f"},
            {"type": "move_progress", "ply": 1, "move": "DUPLICATE"},  # duplicate ply -> skipped
            {"type": "move_progress", "ply": 2, "move": "3c3d"},
            {"type": "move_progress", "ply": 1, "move": "LATE"},  # out-of-order ply -> skipped
        ]
    )
    assert moves == ["7g7f", "3c3d"]
    assert seen == 2


def test_remote_move_aggregation_moveless_event_does_not_block_later_move() -> None:
    # A move-less terminal event must not advance last_ply_seen, otherwise a real move arriving
    # later at the same ply would be dropped.
    moves, seen = _aggregate(
        [
            {"type": "move_progress", "ply": 1, "game_result": "BLACK_WIN"},
            {"type": "move_progress", "ply": 1, "move": "7g7f"},
        ]
    )
    assert moves == ["7g7f"]
    assert seen == 1


@pytest.mark.asyncio
async def test_run_items_concurrently_rejects_nonpositive_limit() -> None:
    async def _run_one(_item: int) -> None:
        return None

    with pytest.raises(ValueError, match="concurrency_limit"):
        await run_items_concurrently(
            stop_event=asyncio.Event(),
            worker_tasks=set(),
            running_tasks=set(),
            items=[1, 2],
            run_one=_run_one,
            concurrency_limit=0,
        )


@pytest.mark.asyncio
async def test_run_items_concurrently_isolates_failure_and_clears_tasks() -> None:
    worker_tasks: set[asyncio.Task[None]] = set()
    running_tasks: set[asyncio.Task[None]] = set()
    stop_event = asyncio.Event()

    async def _run_one(item: int) -> None:
        if item == 0:
            raise ValueError("item failed")
        await asyncio.sleep(0.01)

    with pytest.raises(ValueError, match="item failed"):
        await run_items_concurrently(
            stop_event=stop_event,
            worker_tasks=worker_tasks,
            running_tasks=running_tasks,
            items=list(range(6)),
            run_one=_run_one,
            concurrency_limit=2,
        )

    # All workers were awaited and removed; no detached tasks leak after the failure.
    assert worker_tasks == set()
    assert running_tasks == set()
    assert stop_event.is_set()
