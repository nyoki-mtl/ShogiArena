"""Progress and engine-IO event helpers for GameRunner."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal

from rsshogi.core import Move

from shogiarena._core.contexts.match.ports.game_engine_ports import EngineLifecycleEventPort, GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkResultPort
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent
from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .runner_types import (
    _ClockStartPayload,
    _EngineIoBatchEntry,
    _EngineIoBatchPayload,
    _EngineIoGameMeta,
    _EngineStatePayload,
    _MoveProgressPayload,
    _ProgressPayload,
)

logger = logging.getLogger(__name__)

# Raw USI transcript is telemetry: when a consumer falls behind, dropping transcript lines keeps the
# run correct, whereas dropping move/clock/result events would leave the dashboard with an incomplete
# game.
ENGINE_IO_QUEUE_BACKLOG_LIMIT = 50_000

# Warn on the first dropped batch, then throttle to avoid log spam under sustained backlog.
ENGINE_IO_DROP_WARN_EVERY = 1_000

# Raw engine I/O is coalesced into time windows before serialization so downstream cost does not
# scale with engine ``info`` line frequency (task 0046). A window flushes on the first of: reaching
# the line cap, or the flush interval elapsing since the window's first buffered line.
ENGINE_IO_BATCH_MAX_LINES = 128
ENGINE_IO_FLUSH_INTERVAL_MS = 50.0


class GameRunnerProgressMixin:
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None

    _extract_evaluation: Any
    _extract_search_statistics: Any
    # Batches dropped because the progress consumer fell behind (backlog).
    _dropped_engine_io_events: int = 0

    # Engine-I/O batching state. Initialized lazily so the mixin needs no cooperative __init__.
    _engine_io_buffers: dict[tuple[str, str], list[_EngineIoBatchEntry]]
    _engine_io_removes: dict[tuple[str, str], Callable[[], None]]
    _engine_io_meta: dict[str, _EngineIoGameMeta]
    _engine_io_lines_ingested: int = 0
    _engine_io_lines_dropped: int = 0
    _engine_io_batches_emitted: int = 0
    # Demand predicate (game_id -> raw I/O wanted). None = always collect (non-dashboard runs).
    _engine_io_wanted: Callable[[str], bool] | None = None

    def set_engine_io_wanted(self, predicate: Callable[[str], bool] | None) -> None:
        """raw engine-I/O を収集すべきかの demand predicate を注入する（dashboard 購読状態など）。"""
        self._engine_io_wanted = predicate

    def _engine_io_state(self) -> dict[tuple[str, str], list[_EngineIoBatchEntry]]:
        buffers = self.__dict__.get("_engine_io_buffers")
        if buffers is None:
            buffers = {}
            self._engine_io_buffers = buffers
            self._engine_io_removes = {}
            self._engine_io_meta = {}
        return buffers

    def _engine_io_demand(self, game_id: str) -> bool:
        predicate = self._engine_io_wanted
        if predicate is None:
            return True
        try:
            return bool(predicate(game_id))
        except Exception:
            # Fail open: a broken demand signal must not silently starve telemetry.
            logger.warning("engine-I/O demand predicate failed for game %s", game_id, exc_info=True)
            return True

    async def _enqueue_progress(self, game_id: str | None, ply: int, payload: _ProgressPayload) -> None:
        if self.progress_queue is None or game_id is None:
            return
        numeric_id = self._progress_numeric_id(game_id)
        await self.progress_queue.put((numeric_id, ply, json.dumps(payload, ensure_ascii=False)))

    def _should_drop_engine_io(self) -> bool:
        queue = self.progress_queue
        if queue is None:
            return True
        if queue.qsize() < ENGINE_IO_QUEUE_BACKLOG_LIMIT:
            return False
        self._dropped_engine_io_events += 1
        if self._dropped_engine_io_events % ENGINE_IO_DROP_WARN_EVERY == 1:
            logger.warning(
                "Progress consumer is behind; dropped %d engine I/O batch(es), %d line(s) (queue=%d)",
                self._dropped_engine_io_events,
                self._engine_io_lines_dropped,
                queue.qsize(),
            )
        return True

    async def _enqueue_clock_start(
        self,
        *,
        game_id: str | None,
        ply: int,
        start_ply_number: int,
        is_active_black: bool,
        black_remaining_ms: int,
        white_remaining_ms: int,
        time_control_black: str,
        time_control_white: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> None:
        payload: _ClockStartPayload = {
            "type": "clock_start",
            "game_id": game_id,
            "active": "black" if is_active_black else "white",
            "black_remain_ms": black_remaining_ms,
            "white_remain_ms": white_remaining_ms,
            "started_at_ms": int(time.time() * 1000),
            "time_control_black": time_control_black,
            "time_control_white": time_control_white,
            "byoyomi_ms_black": int(black_limits.byoyomi_ms or 0),
            "byoyomi_ms_white": int(white_limits.byoyomi_ms or 0),
            "increment_ms_black": int(black_limits.increment_ms or 0),
            "increment_ms_white": int(white_limits.increment_ms or 0),
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "start_ply_number": start_ply_number,
        }
        await self._enqueue_progress(game_id, ply, payload)

    async def _enqueue_move_progress(
        self,
        *,
        game_id: str | None,
        ply_index: int,
        start_ply_number: int,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        board_sfen: str,
        usi_move: str,
        ki2_move: str,
        eval_cp: int | None,
        depth: int | None,
        seldepth: int | None,
        nodes: int | None,
        time_ms: int | None,
        wall_time_ms: int | None = None,
        engine_wall_time_ms: int | None = None,
    ) -> None:
        display_ply = max(0, start_ply_number - 1) + ply_index
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": board_sfen,
            "move": usi_move,
            "ki2_move": ki2_move,
            "eval_cp": eval_cp,
            "ply": ply_index,
            "display_ply": display_ply,
            "current_ply": ply_index,
            "start_ply_number": start_ply_number,
            "depth": depth,
            "seldepth": seldepth,
            "nodes": nodes,
            "time_ms": time_ms,
            "wall_time_ms": wall_time_ms,
            "engine_wall_time_ms": engine_wall_time_ms,
        }
        await self._enqueue_progress(game_id, ply_index, payload)

    async def _enqueue_game_result(
        self,
        *,
        game_id: str | None,
        moves: list[Move],
        result: GameResult,
        initial_sfen: str,
        final_sfen: str,
        black_name: str,
        white_name: str,
        start_ply_number: int,
    ) -> None:
        if self.progress_queue is None or game_id is None:
            return
        completion_index = len(moves)
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": final_sfen,
            "start_ply_number": start_ply_number,
            "ply": completion_index,
            "current_ply": completion_index,
            "game_result": game_result_name(result),
        }
        await self._enqueue_progress(
            game_id,
            completion_index,
            payload,
        )

    def _ingest_engine_io_line(
        self,
        *,
        game_id: str | None,
        role: Literal["black", "white"],
        entry: UsiIoEvent,
    ) -> None:
        """Raw USI 行を per-(game, role) の time-window buffer へ積む（0046）。

        move/clock/result と異なり lossy telemetry として扱い、window を1つの
        ``engine_io_batch`` へ coalesce することで下流コストを engine の ``info`` 行頻度から切り離す。
        """
        if game_id is None or self.progress_queue is None:
            return
        buffers = self._engine_io_state()
        self._engine_io_lines_ingested += 1
        key = (game_id, role)
        buffer = buffers.get(key)
        if buffer is None:
            buffer = []
            buffers[key] = buffer
        timestamp = entry.timestamp_ms if entry.timestamp_ms is not None else int(time.time() * 1000)
        line_entry: _EngineIoBatchEntry = {
            "direction": entry.direction,
            "line": entry.line,
            "ts": timestamp,
        }
        if entry.phase:
            line_entry["state"] = entry.phase
        buffer.append(line_entry)
        if len(buffer) >= ENGINE_IO_BATCH_MAX_LINES:
            # Size-cap flush bounds memory/latency under bursts; the per-game supervisor task
            # handles the time-window (interval) flush.
            self._flush_engine_io_buffer(game_id, role)

    def _flush_engine_io_buffer(self, game_id: str, role: Literal["black", "white"]) -> None:
        """1 window 分の buffer を1つの ``engine_io_batch`` として enqueue する。

        queue は unbounded なので ``put_nowait`` で同期に流せる。consumer 遅延時は
        window ごと破棄し、破棄した行数を counter へ記録する（telemetry は backpressure しない）。
        """
        buffers = self._engine_io_state()
        key = (game_id, role)
        queue = self.progress_queue
        if queue is None:
            return
        buffer = buffers.get(key)
        if not buffer:
            return
        buffers[key] = []
        if self._should_drop_engine_io():
            self._engine_io_lines_dropped += len(buffer)
            return
        payload: _EngineIoBatchPayload = {
            "type": "engine_io_batch",
            "game_id": game_id,
            "role": role,
            "entries": buffer,
        }
        meta = self._engine_io_meta.get(game_id)
        if meta is not None:
            payload["initial_sfen"] = meta.initial_sfen
            payload["black_name"] = meta.black_name
            payload["white_name"] = meta.white_name
        self._engine_io_batches_emitted += 1
        numeric_id = self._progress_numeric_id(game_id)
        queue.put_nowait((numeric_id, 0, json.dumps(payload, ensure_ascii=False)))

    def _flush_all_engine_io_buffers(self, game_id: str) -> None:
        for role in ("black", "white"):
            self._flush_engine_io_buffer(game_id, role)
        buffers = self._engine_io_state()
        for role in ("black", "white"):
            buffers.pop((game_id, role), None)
            remove = self._engine_io_removes.pop((game_id, role), None)
            if remove is not None:
                remove()
        self._engine_io_meta.pop(game_id, None)

    def _attach_engine_io(
        self,
        engine: GameEnginePort,
        role: Literal["black", "white"],
        game_id: str,
    ) -> None:
        """raw I/O listener を登録する（未登録時のみ）。demand true の間だけ attach される。"""
        buffers = self._engine_io_state()
        key = (game_id, role)
        if key in self._engine_io_removes:
            return

        # Declared with ``async def`` on purpose: the session dispatches a plain ``def`` handler
        # through ``asyncio.to_thread``, which would cost a thread round-trip per USI line.
        async def handler(entry: UsiIoEvent) -> None:
            self._ingest_engine_io_line(game_id=game_id, role=role, entry=entry)

        remove = engine.register_io_log_handler(handler)
        self._engine_io_removes[key] = remove if callable(remove) else (lambda: None)
        buffers.setdefault(key, [])

    def _detach_engine_io(self, game_id: str, role: Literal["black", "white"]) -> None:
        """raw I/O listener を解除し、残 buffer を final-flush する。"""
        self._engine_io_state()
        key = (game_id, role)
        remove = self._engine_io_removes.pop(key, None)
        if remove is None:
            return
        remove()
        # Not registered anymore, but a window may still be buffered; flush it before it is lost.
        self._flush_engine_io_buffer(game_id, role)

    async def _engine_io_supervisor(
        self,
        engines: dict[Literal["black", "white"], GameEnginePort],
        game_id: str,
    ) -> None:
        """demand を polling して raw I/O listener を動的に付け外しし、window を flush する（0046 A3-2）。

        demand false の間は listener を登録しないため、``_emit_io_log`` が handler 未登録で早期 return し
        per-line コストが消える。demand true の focused game だけが raw 行を流す。
        """
        interval = ENGINE_IO_FLUSH_INTERVAL_MS / 1000.0
        while True:
            try:
                wanted = self._engine_io_demand(game_id)
                for role, engine in engines.items():
                    if wanted:
                        self._attach_engine_io(engine, role, game_id)
                    else:
                        self._detach_engine_io(game_id, role)
                if wanted:
                    for role in ("black", "white"):
                        self._flush_engine_io_buffer(game_id, role)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A per-tick failure must not kill gating for the rest of the game.
                logger.warning("engine-I/O supervisor tick failed for game %s", game_id, exc_info=True)
            await asyncio.sleep(interval)

    def _register_engine_lifecycle_listener(
        self,
        engine: GameEnginePort,
        role: Literal["black", "white"],
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> Callable[[], None]:
        """engine の状態遷移だけを拾い、バッジ用の ``engine_state`` を安価に発行する（0046）。

        raw I/O 行から独立し per-transition（~2/手）のみ発火するため、engine_status バッジを
        engine_io の per-line 処理から切り離せる。lifecycle handler は同期呼び出しされるので
        ``put_nowait`` のみ行い ``await`` しない。
        """
        queue = self.progress_queue
        if game_id is None or queue is None:
            return lambda: None
        numeric_id = self._progress_numeric_id(game_id)

        def handler(event: EngineLifecycleEventPort) -> None:
            # ``state_changed`` fires on every engine transition and is the badge's sole feed. Other
            # named events are redundant for the badge, except ``new_game`` which carries the kickoff
            # command into the io_tail so the card UI can hide the prep overlay (card-data
            # hasKickoffCommand) without the raw ``info`` flood (0046 A3).
            name = event.name
            if name not in {"state_changed", "new_game"}:
                return
            state = event.state
            if not state:
                return
            payload: _EngineStatePayload = {
                "type": "engine_state",
                "game_id": game_id,
                "initial_sfen": initial_sfen,
                "black_name": black_name,
                "white_name": white_name,
                "role": role,
                "state": state,
                "ts": int(time.time() * 1000),
            }
            if name == "new_game":
                payload["direction"] = "out"
                payload["line"] = "usinewgame"
            queue.put_nowait((numeric_id, 0, json.dumps(payload, ensure_ascii=False)))

        remove = engine.register_lifecycle_handler(handler)
        return remove if callable(remove) else lambda: None

    @contextmanager
    def _engine_io_listener_context(
        self,
        black_engine: GameEnginePort,
        white_engine: GameEnginePort,
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> Iterator[None]:
        # Lifecycle listeners feed the engine_status badge cheaply and stay attached for the whole
        # game. Raw I/O listeners carry the high-frequency transcript and are attached dynamically by
        # the supervisor only while a client subscribes to this game's raw I/O.
        lifecycle_cleanups: list[Callable[[], None]] = [
            self._register_engine_lifecycle_listener(
                black_engine, "black", game_id, initial_sfen, black_name, white_name
            ),
            self._register_engine_lifecycle_listener(
                white_engine, "white", game_id, initial_sfen, black_name, white_name
            ),
        ]
        supervisor: asyncio.Task[None] | None = None
        if game_id is not None and self.progress_queue is not None:
            self._engine_io_state()
            self._engine_io_meta[game_id] = _EngineIoGameMeta(initial_sfen, black_name, white_name)
            engines: dict[Literal["black", "white"], GameEnginePort] = {
                "black": black_engine,
                "white": white_engine,
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                supervisor = loop.create_task(self._engine_io_supervisor(engines, game_id))
            else:
                # No running loop (should not happen in real runs): attach statically as a fallback.
                self._attach_engine_io(black_engine, "black", game_id)
                self._attach_engine_io(white_engine, "white", game_id)
        try:
            yield
        finally:
            if supervisor is not None:
                supervisor.cancel()
            # Only tear down raw-I/O state when it was set up (progress_queue present); non-dashboard
            # runs never allocated it.
            if game_id is not None and self.progress_queue is not None:
                self._detach_engine_io(game_id, "black")
                self._detach_engine_io(game_id, "white")
                self._flush_all_engine_io_buffers(game_id)
            for cleanup in lifecycle_cleanups:
                cleanup()

    async def _enqueue_terminal_progress(
        self,
        *,
        game_id: str | None,
        ply_index: int,
        start_ply_number: int,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        board_sfen: str,
        result: GameResult,
        think_result: UsiThinkResultPort,
        elapsed_ms: int,
        engine_wall_time_ms: int | None = None,
    ) -> None:
        if self.progress_queue is None or game_id is None:
            return
        eval_cp = self._extract_evaluation(think_result)
        search_stats = self._extract_search_statistics(think_result, elapsed_ms)
        display_ply = max(0, start_ply_number - 1) + ply_index
        payload: _MoveProgressPayload = {
            "type": "move_progress",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "sfen": board_sfen,
            "ply": ply_index,
            "display_ply": display_ply,
            "current_ply": ply_index,
            "start_ply_number": start_ply_number,
            "game_result": game_result_name(result),
            "eval_cp": eval_cp,
            "depth": search_stats["depth"],
            "seldepth": search_stats["seldepth"],
            "nodes": search_stats["nodes"],
            "time_ms": search_stats["time_ms"],
            "wall_time_ms": int(elapsed_ms),
            "engine_wall_time_ms": engine_wall_time_ms,
        }
        await self._enqueue_progress(game_id, ply_index, payload)

    def _progress_numeric_id(self, game_id: str) -> int:
        """Convert textual game_id to numeric form for progress tracking."""
        if game_id.startswith("game_"):
            suffix = game_id[len("game_") :]
            compact_suffix = suffix.replace("_", "")
            if compact_suffix.isdigit():
                return int(compact_suffix)
        return zlib.crc32(game_id.encode("utf-8")) & 0x7FFFFFFF
