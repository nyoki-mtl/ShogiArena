from __future__ import annotations

import asyncio
import json
import zlib
from contextlib import suppress

import pytest

from shogiarena._core.contexts.game_session.application.progress.consumption import (
    ProgressState,
    consume_progress_loop,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    WorkerSnapshotModel,
)


def _numeric_game_id(game_id: str) -> int:
    return zlib.crc32(game_id.encode("utf-8")) & 0x7FFFFFFF


def _preassign_worker(
    numeric_game_id: int,
    num_workers: int,
    game_to_worker: dict[int, int],
    worker_busy: set[int],
) -> int | None:
    if numeric_game_id in game_to_worker:
        return game_to_worker[numeric_game_id]
    for idx in range(num_workers):
        if idx not in worker_busy:
            game_to_worker[numeric_game_id] = idx
            worker_busy.add(idx)
            return idx
    return None


def _worker_snapshot(game_id: str) -> WorkerSnapshotModel:
    return WorkerSnapshotModel(
        game_id=game_id,
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        current_ply=0,
        sfen="startpos",
    )


async def _run_consumer_once(
    *,
    state: ProgressState,
    events: list[tuple[int, int, dict[str, object]]],
) -> None:
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    task = asyncio.create_task(
        consume_progress_loop(
            progress_queue=progress_queue,
            state=state,
            preassign_worker=_preassign_worker,
            api_server=None,
            broadcast_snapshot_and_diff=lambda *_: None,
        )
    )
    try:
        for game_id_num, move_count, payload in events:
            progress_queue.put_nowait((game_id_num, move_count, json.dumps(payload)))
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_late_event_after_result_does_not_reacquire_worker() -> None:
    game_id = "g-timeout"
    numeric_id = _numeric_game_id(game_id)
    state = ProgressState(
        num_workers=1,
        game_to_worker={numeric_id: 0},
        worker_busy={0},
        worker_snapshots={0: _worker_snapshot(game_id)},
    )

    await _run_consumer_once(
        state=state,
        events=[
            (
                numeric_id,
                40,
                {
                    "type": "move_progress",
                    "game_id": game_id,
                    "initial_sfen": "startpos",
                    "sfen": "startpos",
                    "game_result": "WHITE_WIN_BY_TIMEOUT",
                },
            ),
            (
                numeric_id,
                41,
                {
                    "type": "clock_increment",
                    "game_id": game_id,
                    "black_remain_ms": 0,
                    "white_remain_ms": 0,
                    "occurred_at_ms": 1,
                },
            ),
        ],
    )

    assert state.game_to_worker == {}
    assert state.worker_busy == set()


@pytest.mark.asyncio
async def test_unassigned_non_bootstrap_event_is_dropped_without_worker_assignment() -> None:
    game_id = "g-unassigned"
    numeric_id = _numeric_game_id(game_id)
    state = ProgressState(
        num_workers=1,
        game_to_worker={},
        worker_busy=set(),
        worker_snapshots={},
    )

    await _run_consumer_once(
        state=state,
        events=[
            (
                numeric_id,
                1,
                {
                    "type": "clock_increment",
                    "game_id": game_id,
                    "black_remain_ms": 1000,
                    "white_remain_ms": 900,
                    "occurred_at_ms": 123,
                },
            ),
        ],
    )

    assert state.game_to_worker == {}
    assert state.worker_busy == set()
    assert state.worker_snapshots == {}
