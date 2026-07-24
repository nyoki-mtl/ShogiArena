#!/usr/bin/env python3
"""Launch ShogiArena with low-overhead Phase 0 runtime-stall instrumentation."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import functools
import gc
import json
import os
import sys
import threading
import time
import weakref
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar, cast

_T = TypeVar("_T")
_GAME_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("runtime_stall_game_id", default=None)


class ProbeWriter:
    """Serialize probe events as line-buffered JSONL."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("x", encoding="utf-8", buffering=1, newline="\n")
        self._lock = threading.Lock()
        self.started_ns = time.perf_counter_ns()

    def emit(self, event: str, **fields: object) -> None:
        payload = {
            "event": event,
            "mono_ns": time.perf_counter_ns(),
            "wall_ns": time.time_ns(),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            **fields,
        }
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._stream.write(f"{line}\n")

    def close(self) -> None:
        with self._lock:
            self._stream.close()


class ProbeState:
    """Mutable process-local probe registry."""

    def __init__(self, writer: ProbeWriter, *, interval_ms: float, threshold_ms: float) -> None:
        self.writer = writer
        self.interval_s = interval_ms / 1000.0
        self.threshold_ms = threshold_ms
        self.stop_event = threading.Event()
        self.queues: list[tuple[str, weakref.ReferenceType[asyncio.Queue[Any]]]] = []
        self.completed_games = 0
        self.io_handler_calls = 0
        self.io_handler_total_ms = 0.0
        self.io_handler_max_ms = 0.0
        self.event_loop: asyncio.AbstractEventLoop | None = None
        self._queue_ids: set[int] = set()
        self._watchdog: threading.Thread | None = None

    def register_queue(self, name: str, queue: asyncio.Queue[Any] | None) -> None:
        if queue is None or id(queue) in self._queue_ids:
            return
        self._queue_ids.add(id(queue))
        self.queues.append((name, weakref.ref(queue)))
        self.writer.emit("queue_registered", queue=name, queue_id=id(queue), maxsize=queue.maxsize)

    def queue_snapshot(self) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        alive: list[tuple[str, weakref.ReferenceType[asyncio.Queue[Any]]]] = []
        for name, reference in self.queues:
            queue = reference()
            if queue is None:
                continue
            alive.append((name, reference))
            snapshot[name] = snapshot.get(name, 0) + queue.qsize()
        self.queues = alive
        return snapshot

    def start_watchdog(self) -> None:
        if self._watchdog is not None:
            return
        self._watchdog = threading.Thread(target=self._watchdog_main, name="runtime-stall-watchdog", daemon=True)
        self._watchdog.start()

    def _watchdog_main(self) -> None:
        expected = time.perf_counter() + self.interval_s
        sample_deadline = expected
        while not self.stop_event.wait(max(0.0, expected - time.perf_counter())):
            now = time.perf_counter()
            overshoot_ms = max(0.0, (now - expected) * 1000.0)
            if overshoot_ms >= self.threshold_ms:
                self.writer.emit("thread_overshoot", duration_ms=overshoot_ms)
            if now >= sample_deadline:
                self.writer.emit(
                    "process_sample",
                    completed_games=self.completed_games,
                    event_loop=self.event_loop_snapshot(),
                    gc_count=list(gc.get_count()),
                    io_handler_calls=self.io_handler_calls,
                    io_handler_max_ms=self.io_handler_max_ms,
                    io_handler_total_ms=self.io_handler_total_ms,
                    queues=self.queue_snapshot(),
                    rss_bytes=_read_rss_bytes(),
                    thread_count=threading.active_count(),
                )
                sample_deadline = now + 1.0
            expected = max(expected + self.interval_s, now)

    def event_loop_snapshot(self) -> dict[str, int | None]:
        loop = self.event_loop
        if loop is None:
            return {"ready": None, "scheduled": None, "executor_pending": None}
        ready = getattr(loop, "_ready", None)
        scheduled = getattr(loop, "_scheduled", None)
        executor = getattr(loop, "_default_executor", None)
        work_queue = getattr(executor, "_work_queue", None)
        return {
            "ready": len(ready) if ready is not None else None,
            "scheduled": len(scheduled) if scheduled is not None else None,
            "executor_pending": work_queue.qsize() if work_queue is not None else None,
        }

    def stop(self) -> None:
        self.stop_event.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)


class InstrumentedLock:
    """Async lock proxy that records acquisition wait and hold duration."""

    def __init__(self, lock: asyncio.Lock, state: ProbeState) -> None:
        self._lock = lock
        self._state = state
        self._acquired_ns: contextvars.ContextVar[int | None] = contextvars.ContextVar(
            f"runtime_stall_lock_acquired_{id(self)}", default=None
        )

    async def acquire(self) -> bool:
        started = time.perf_counter_ns()
        result = await self._lock.acquire()
        acquired = time.perf_counter_ns()
        self._acquired_ns.set(acquired)
        self._state.writer.emit(
            "completion_lock_wait",
            duration_ms=(acquired - started) / 1_000_000.0,
            game_id=_GAME_ID.get(),
        )
        return result

    def release(self) -> None:
        released = time.perf_counter_ns()
        acquired = self._acquired_ns.get()
        if acquired is not None:
            self._state.writer.emit(
                "completion_lock_hold",
                duration_ms=(released - acquired) / 1_000_000.0,
                game_id=_GAME_ID.get(),
            )
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


def _read_rss_bytes() -> int | None:
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


async def _loop_heartbeat(state: ProbeState) -> None:
    expected = time.perf_counter() + state.interval_s
    sample_deadline = expected
    while not state.stop_event.is_set():
        await asyncio.sleep(max(0.0, expected - time.perf_counter()))
        now = time.perf_counter()
        lag_ms = max(0.0, (now - expected) * 1000.0)
        if lag_ms >= state.threshold_ms:
            state.writer.emit("loop_lag", duration_ms=lag_ms)
        if now >= sample_deadline:
            state.writer.emit("loop_sample", lag_ms=lag_ms)
            sample_deadline = now + 1.0
        expected = max(expected + state.interval_s, now)


def _timed_sync(state: ProbeState, cls: type[Any], method_name: str) -> None:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, "_runtime_stall_wrapped", False):
        return

    @functools.wraps(original)
    def wrapped(*args: object, **kwargs: object) -> object:
        started = time.perf_counter_ns()
        try:
            return original(*args, **kwargs)
        finally:
            state.writer.emit(
                "completion_step",
                step=method_name,
                duration_ms=(time.perf_counter_ns() - started) / 1_000_000.0,
                game_id=_GAME_ID.get(),
            )

    wrapped._runtime_stall_wrapped = True  # type: ignore[attr-defined]
    setattr(cls, method_name, wrapped)


def _timed_async(state: ProbeState, cls: type[Any], method_name: str) -> None:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, "_runtime_stall_wrapped", False):
        return

    @functools.wraps(original)
    async def wrapped(*args: object, **kwargs: object) -> object:
        started = time.perf_counter_ns()
        try:
            return await cast(Callable[..., Awaitable[object]], original)(*args, **kwargs)
        finally:
            state.writer.emit(
                "completion_step",
                step=method_name,
                duration_ms=(time.perf_counter_ns() - started) / 1_000_000.0,
                game_id=_GAME_ID.get(),
            )

    wrapped._runtime_stall_wrapped = True  # type: ignore[attr-defined]
    setattr(cls, method_name, wrapped)


def _install_gc_probe(state: ProbeState) -> Callable[[], None]:
    starts: dict[int, list[int]] = {}

    def callback(phase: str, info: dict[str, int]) -> None:
        generation = int(info.get("generation", -1))
        if phase == "start":
            starts.setdefault(generation, []).append(time.perf_counter_ns())
            return
        stack = starts.get(generation)
        started = stack.pop() if stack else None
        duration_ms = (time.perf_counter_ns() - started) / 1_000_000.0 if started is not None else None
        if generation == 2 or duration_ms is None or duration_ms >= state.threshold_ms:
            state.writer.emit(
                "gc_pause",
                generation=generation,
                duration_ms=duration_ms,
                collected=info.get("collected"),
                uncollectable=info.get("uncollectable"),
            )

    gc.callbacks.append(callback)

    def remove() -> None:
        if callback in gc.callbacks:
            gc.callbacks.remove(callback)

    return remove


def _install_runtime_patches(
    state: ProbeState,
    *,
    io_listener_mode: str,
    option_summary_mode: str,
) -> None:
    from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import (
        BaseOrchestrator,
    )
    from shogiarena._core.contexts.game_session.application.completion.session_service import (
        TournamentSessionCompletionService,
    )
    from shogiarena._core.contexts.match.application.runner_progress_mixin import GameRunnerProgressMixin
    from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
    from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine

    original_initialize = BaseOrchestrator._initialize_common_components

    @functools.wraps(original_initialize)
    def initialize(self: object, *args: object, **kwargs: object) -> object:
        result = original_initialize(self, *args, **kwargs)
        state.register_queue("progress", getattr(self, "progress_queue", None))
        if option_summary_mode == "disabled" and getattr(self, "api_server", None) is None:
            cast(Any, self)._summary_updater = None
        return result

    BaseOrchestrator._initialize_common_components = initialize

    original_dispatcher = AsyncUsiEngine._ensure_io_log_dispatcher

    @functools.wraps(original_dispatcher)
    def ensure_dispatcher(self: object, *args: object, **kwargs: object) -> object:
        result = original_dispatcher(self, *args, **kwargs)
        state.register_queue("engine_io_dispatch", getattr(self, "_io_log_dispatch_queue", None))
        return result

    AsyncUsiEngine._ensure_io_log_dispatcher = ensure_dispatcher

    original_io_handler = AsyncUsiEngine._run_io_log_handler

    @functools.wraps(original_io_handler)
    async def run_io_handler(self: object, *args: object, **kwargs: object) -> object:
        started = time.perf_counter_ns()
        try:
            return await original_io_handler(self, *args, **kwargs)
        finally:
            duration_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            state.io_handler_calls += 1
            state.io_handler_total_ms += duration_ms
            state.io_handler_max_ms = max(state.io_handler_max_ms, duration_ms)

    AsyncUsiEngine._run_io_log_handler = run_io_handler

    if io_listener_mode == "async-direct":
        AsyncUsiEngine._is_async_io_log_handler = staticmethod(lambda _handler: True)
    elif io_listener_mode == "disabled":

        @contextlib.contextmanager
        def engine_io_listener_context(*_args: object, **_kwargs: object) -> Any:
            yield

        GameRunnerProgressMixin._engine_io_listener_context = engine_io_listener_context

    original_completion = TournamentRunner._handle_game_completion

    @functools.wraps(original_completion)
    async def handle_completion(self: object, game_spec: object, *args: object, **kwargs: object) -> object:
        game_id = str(getattr(game_spec, "game_id", "unknown"))
        token = _GAME_ID.set(game_id)
        started = time.perf_counter_ns()
        try:
            return await original_completion(self, game_spec, *args, **kwargs)
        finally:
            state.completed_games += 1
            state.writer.emit(
                "completion_total",
                duration_ms=(time.perf_counter_ns() - started) / 1_000_000.0,
                game_id=game_id,
                completed_games=state.completed_games,
                queues=state.queue_snapshot(),
            )
            _GAME_ID.reset(token)

    TournamentRunner._handle_game_completion = handle_completion

    original_run = TournamentRunner.run

    @functools.wraps(original_run)
    async def run(self: object, *args: object, **kwargs: object) -> object:
        state.event_loop = asyncio.get_running_loop()
        runner = cast(Any, self)
        runner_state = runner._state
        completion_lock = runner_state.completion_lock
        if not isinstance(completion_lock, InstrumentedLock):
            runner_state.completion_lock = InstrumentedLock(completion_lock, state)
        heartbeat = asyncio.create_task(_loop_heartbeat(state), name="runtime-stall-loop-heartbeat")
        try:
            return await original_run(self, *args, **kwargs)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    TournamentRunner.run = run

    for method_name in (
        "enrich_record_and_summarize",
        "persist_record_outputs",
        "update_rating_state",
        "commit_completion_state",
    ):
        _timed_sync(state, TournamentSessionCompletionService, method_name)
    for method_name in ("sync_openbench", "process_game_completion"):
        _timed_async(state, TournamentSessionCompletionService, method_name)
    _timed_sync(state, TournamentRunner, "_save_run_state")


def _parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-output", type=Path, required=True)
    parser.add_argument("--interval-ms", type=float, default=20.0)
    parser.add_argument("--lag-threshold-ms", type=float, default=10.0)
    parser.add_argument(
        "--io-listener-mode",
        choices=("baseline", "async-direct", "disabled"),
        default="baseline",
    )
    parser.add_argument(
        "--option-summary-mode",
        choices=("baseline", "disabled"),
        default="baseline",
    )
    parser.add_argument("shogiarena_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = list(args.shogiarena_args)
    if forwarded and forwarded[0] == "--":
        forwarded.pop(0)
    if not forwarded:
        parser.error("ShogiArena arguments are required after --")
    if args.interval_ms <= 0 or args.lag_threshold_ms < 0:
        parser.error("interval must be positive and threshold must be non-negative")
    return args, forwarded


def main() -> int:
    """Install probes and invoke the regular ShogiArena CLI."""
    args, forwarded = _parse_args()
    writer = ProbeWriter(args.probe_output)
    state = ProbeState(writer, interval_ms=args.interval_ms, threshold_ms=args.lag_threshold_ms)
    remove_gc_probe = _install_gc_probe(state)
    state.start_watchdog()
    writer.emit(
        "probe_start",
        argv=forwarded,
        interval_ms=args.interval_ms,
        io_listener_mode=args.io_listener_mode,
        lag_threshold_ms=args.lag_threshold_ms,
        option_summary_mode=args.option_summary_mode,
        python=sys.version,
    )
    exit_code = 0
    try:
        _install_runtime_patches(
            state,
            io_listener_mode=args.io_listener_mode,
            option_summary_mode=args.option_summary_mode,
        )
        from shogiarena.cli import main as shogiarena_main

        sys.argv = ["shogiarena", *forwarded]
        shogiarena_main()
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        state.stop()
        remove_gc_probe()
        writer.emit(
            "probe_stop",
            completed_games=state.completed_games,
            exit_code=exit_code,
            queues=state.queue_snapshot(),
        )
        writer.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
