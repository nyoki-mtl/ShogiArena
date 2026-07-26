from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable

import pytest

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.application.runner_progress_mixin import (
    ENGINE_IO_BATCH_MAX_LINES,
    ENGINE_IO_FLUSH_INTERVAL_MS,
    ENGINE_IO_QUEUE_BACKLOG_LIMIT,
)
from shogiarena._core.contexts.match.application.runner_types import _EngineIoGameMeta
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent


def _io_event(line: str = "info depth 12 score cp 30 pv 7g7f") -> UsiIoEvent:
    return UsiIoEvent(direction="in", line=line, phase=None)


class _FakeEngine:
    """Records raw-I/O handler registrations and removals for gating assertions."""

    def __init__(self, role: str = "black") -> None:
        self.role = role
        self.io_handlers: list[object] = []
        self.attached = False

    def register_io_log_handler(self, handler: object) -> Callable[[], None]:
        self.io_handlers.append(handler)
        self.attached = True

        def _remove() -> None:
            self.attached = False

        return _remove


def _set_meta(runner: GameRunner, game_id: str = "g0001") -> None:
    runner._engine_io_state()  # noqa: SLF001
    runner._engine_io_meta[game_id] = _EngineIoGameMeta("startpos", "a", "b")  # noqa: SLF001


@pytest.mark.asyncio
async def test_engine_io_ingest_is_noop_without_a_consumer() -> None:
    runner = GameRunner(progress_queue=None)
    # Without a progress queue, ingest is a cheap no-op (nowhere to send telemetry).
    runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event())  # noqa: SLF001
    assert runner.progress_queue is None


@pytest.mark.asyncio
async def test_attach_engine_io_registers_a_coroutine_handler() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)
    engine = _FakeEngine("black")

    runner._attach_engine_io(engine, "black", "g0001")  # type: ignore[arg-type]  # noqa: SLF001

    # A plain def handler would be dispatched through asyncio.to_thread once per USI line.
    assert len(engine.io_handlers) == 1
    assert inspect.iscoroutinefunction(engine.io_handlers[0])
    # Attaching twice is idempotent.
    runner._attach_engine_io(engine, "black", "g0001")  # type: ignore[arg-type]  # noqa: SLF001
    assert len(engine.io_handlers) == 1


@pytest.mark.asyncio
async def test_engine_io_lines_are_coalesced_into_one_batch() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)

    for i in range(3):
        runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event(f"info {i}"))  # noqa: SLF001
    runner._flush_engine_io_buffer("g0001", "black")  # noqa: SLF001

    assert queue.qsize() == 1
    _num_id, _ply, payload = queue.get_nowait()
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["type"] == "engine_io_batch"
    assert parsed["role"] == "black"
    assert [entry["line"] for entry in parsed["entries"]] == ["info 0", "info 1", "info 2"]
    assert parsed["initial_sfen"] == "startpos"
    assert runner._engine_io_lines_ingested == 3  # noqa: SLF001
    assert runner._engine_io_batches_emitted == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_engine_io_flushes_at_the_line_cap() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)

    for i in range(ENGINE_IO_BATCH_MAX_LINES):
        runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event(f"info {i}"))  # noqa: SLF001

    # Reaching the cap flushes without waiting for the supervisor's interval flush.
    assert queue.qsize() == 1
    _num_id, _ply, payload = queue.get_nowait()
    assert payload is not None
    parsed = json.loads(payload)
    assert len(parsed["entries"]) == ENGINE_IO_BATCH_MAX_LINES


@pytest.mark.asyncio
async def test_engine_io_batch_is_dropped_once_the_consumer_falls_behind() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)
    for _ in range(ENGINE_IO_QUEUE_BACKLOG_LIMIT):
        queue.put_nowait((1, 0, None))

    runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event("info 0"))  # noqa: SLF001
    runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event("info 1"))  # noqa: SLF001
    runner._flush_engine_io_buffer("g0001", "black")  # noqa: SLF001

    # The backlogged batch is discarded; its lines are counted for observability.
    assert queue.qsize() == ENGINE_IO_QUEUE_BACKLOG_LIMIT
    assert runner._dropped_engine_io_events == 1  # noqa: SLF001
    assert runner._engine_io_lines_dropped == 2  # noqa: SLF001


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


@pytest.mark.asyncio
async def test_supervisor_attaches_raw_io_only_when_demand_is_true() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)
    demand = {"wanted": False}
    runner.set_engine_io_wanted(lambda _gid: demand["wanted"])
    black = _FakeEngine("black")
    white = _FakeEngine("white")
    engines = {"black": black, "white": white}

    task = asyncio.ensure_future(runner._engine_io_supervisor(engines, "g0001"))  # type: ignore[arg-type]  # noqa: SLF001
    try:
        # Demand false: raw I/O listeners must not be attached (no per-line cost).
        await asyncio.sleep(ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0 + 0.02)
        assert not black.attached and not white.attached

        # Demand rises (a client opens the raw-I/O panel): listeners attach on the next tick.
        demand["wanted"] = True
        await asyncio.sleep(ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0 * 2 + 0.02)
        assert black.attached and white.attached

        # Demand falls: listeners detach again.
        demand["wanted"] = False
        await asyncio.sleep(ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0 * 2 + 0.02)
        assert not black.attached and not white.attached
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_engine_io_demand_defaults_true_and_fails_open() -> None:
    runner = GameRunner(progress_queue=None)
    # No predicate wired: always collect (non-dashboard runs).
    assert runner._engine_io_demand("g0001") is True  # noqa: SLF001

    def _boom(_gid: str) -> bool:
        raise RuntimeError("subscriber lookup failed")

    runner.set_engine_io_wanted(_boom)
    # A broken demand signal must fail open rather than silently starving telemetry.
    assert runner._engine_io_demand("g0001") is True  # noqa: SLF001

    runner.set_engine_io_wanted(lambda _gid: False)
    assert runner._engine_io_demand("g0001") is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_supervisor_flushes_windows_while_attached() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    _set_meta(runner)
    runner.set_engine_io_wanted(lambda _gid: True)
    black = _FakeEngine("black")
    engines = {"black": black}

    task = asyncio.ensure_future(runner._engine_io_supervisor(engines, "g0001"))  # type: ignore[arg-type]  # noqa: SLF001
    try:
        await asyncio.sleep(ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0)  # let it attach
        runner._ingest_engine_io_line(game_id="g0001", role="black", entry=_io_event("info x"))  # noqa: SLF001
        # No size-cap: only the supervisor's interval flush can emit the batch.
        await asyncio.sleep(ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0 * 2 + 0.02)
        assert queue.qsize() >= 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_lifecycle_listener_enqueues_engine_state() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    captured: list[object] = []

    class _LifecycleEngine:
        def register_lifecycle_handler(self, handler: object) -> Callable[[], None]:
            captured.append(handler)
            return lambda: None

    remove = runner._register_engine_lifecycle_listener(  # noqa: SLF001
        engine=_LifecycleEngine(),  # type: ignore[arg-type]
        role="black",
        game_id="g0001",
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )
    assert callable(remove)
    assert len(captured) == 1

    class _Event:
        name = "state_changed"
        state = "waiting_for_bestmove"
        monotonic_ns = 1

    captured[0](_Event())  # type: ignore[operator]

    assert queue.qsize() == 1
    _num_id, _ply, payload = queue.get_nowait()
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["type"] == "engine_state"
    assert parsed["role"] == "black"
    assert parsed["state"] == "waiting_for_bestmove"
    assert parsed["initial_sfen"] == "startpos"


@pytest.mark.asyncio
async def test_lifecycle_new_game_carries_kickoff_command() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    captured: list[object] = []

    class _LifecycleEngine:
        def register_lifecycle_handler(self, handler: object) -> Callable[[], None]:
            captured.append(handler)
            return lambda: None

    runner._register_engine_lifecycle_listener(  # noqa: SLF001
        engine=_LifecycleEngine(),  # type: ignore[arg-type]
        role="black",
        game_id="g0001",
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    class _NewGame:
        name = "new_game"
        state = "ready"
        monotonic_ns = 1

    captured[0](_NewGame())  # type: ignore[operator]
    _num_id, _ply, payload = queue.get_nowait()
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed["type"] == "engine_state"
    # new_game rides a synthetic usinewgame line so the card UI keeps its kickoff signal.
    assert parsed["line"] == "usinewgame"
    assert parsed["direction"] == "out"


@pytest.mark.asyncio
async def test_lifecycle_listener_skips_event_without_state() -> None:
    queue: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=queue)
    captured: list[object] = []

    class _LifecycleEngine:
        def register_lifecycle_handler(self, handler: object) -> Callable[[], None]:
            captured.append(handler)
            return lambda: None

    runner._register_engine_lifecycle_listener(  # noqa: SLF001
        engine=_LifecycleEngine(),  # type: ignore[arg-type]
        role="black",
        game_id="g0001",
        initial_sfen="startpos",
        black_name="a",
        white_name="b",
    )

    class _StatelessEvent:
        name = "state_changed"
        state = None
        monotonic_ns = 1

    captured[0](_StatelessEvent())  # type: ignore[operator]
    assert queue.qsize() == 0

    class _NamedEvent:
        # Named events other than new_game are redundant for the badge (state_changed covers them).
        name = "think_started"
        state = "waiting_for_bestmove"
        monotonic_ns = 2

    captured[0](_NamedEvent())  # type: ignore[operator]
    assert queue.qsize() == 0
