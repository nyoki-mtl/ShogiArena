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

from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkResultPort
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent
from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .runner_types import (
    _ClockStartPayload,
    _EngineIoPayload,
    _MoveProgressPayload,
    _ProgressPayload,
)

logger = logging.getLogger(__name__)

# Raw USI transcript is telemetry: one event per engine output line. When a consumer falls behind,
# dropping transcript lines keeps the run correct, whereas dropping move/clock/result events would
# leave the dashboard with an incomplete game.
ENGINE_IO_QUEUE_BACKLOG_LIMIT = 50_000


class GameRunnerProgressMixin:
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None

    _extract_evaluation: Any
    _extract_search_statistics: Any
    _dropped_engine_io_events: int = 0

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
        if self._dropped_engine_io_events % ENGINE_IO_QUEUE_BACKLOG_LIMIT == 1:
            logger.warning(
                "Progress consumer is behind; dropped %d engine I/O event(s) (queue=%d)",
                self._dropped_engine_io_events,
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

    async def _enqueue_engine_io_event(
        self,
        *,
        game_id: str | None,
        role: Literal["black", "white"],
        entry: UsiIoEvent,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> None:
        if game_id is None or self._should_drop_engine_io():
            return
        timestamp = entry.timestamp_ms if entry.timestamp_ms is not None else int(time.time() * 1000)
        payload: _EngineIoPayload = {
            "type": "engine_io",
            "game_id": game_id,
            "initial_sfen": initial_sfen,
            "black_name": black_name,
            "white_name": white_name,
            "role": role,
            "direction": entry.direction,
            "line": entry.line,
            "ts": timestamp,
        }
        if entry.phase:
            payload["state"] = entry.phase
        await self._enqueue_progress(game_id, 0, payload)

    def _register_engine_io_listener(
        self,
        engine: GameEnginePort,
        role: Literal["black", "white"],
        game_id: str | None,
        initial_sfen: str,
        black_name: str,
        white_name: str,
    ) -> Callable[[], None]:
        # Without a progress consumer the transcript has nowhere to go, and registering the handler
        # would still pay per-line dispatch for every engine output line.
        if game_id is None or self.progress_queue is None:
            return lambda: None

        # Declared with ``async def`` on purpose: the session dispatches a plain ``def`` handler
        # through ``asyncio.to_thread``, which would cost a thread round-trip per USI line.
        async def handler(entry: UsiIoEvent) -> None:
            await self._enqueue_engine_io_event(
                game_id=game_id,
                role=role,
                entry=entry,
                initial_sfen=initial_sfen,
                black_name=black_name,
                white_name=white_name,
            )

        remove = engine.register_io_log_handler(handler)
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
        cleanups: list[Callable[[], None]] = [
            self._register_engine_io_listener(black_engine, "black", game_id, initial_sfen, black_name, white_name),
            self._register_engine_io_listener(white_engine, "white", game_id, initial_sfen, black_name, white_name),
        ]
        try:
            yield
        finally:
            for cleanup in cleanups:
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
