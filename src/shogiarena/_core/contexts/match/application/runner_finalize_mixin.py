"""Finalize/result parsing helpers for GameRunner."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rshogi.core import Board, Move

from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkResultPort
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import GameClock

from .runner_types import _ClockIncrementPayload, _GameMoveResult, _GameOverResult, _MoveContinue

logger = logging.getLogger(__name__)


class GameRunnerFinalizeMixin:
    _is_shutting_down: bool
    _enqueue_progress: Any

    async def _finalize_game(
        self, black_engine: GameEnginePort, white_engine: GameEnginePort, game_result: GameResult
    ) -> None:
        """Notify engines about game result using the protocol abstraction."""
        tasks = {
            "black": black_engine.notify_gameover(game_result),
            "white": white_engine.notify_gameover(game_result),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        failures = {
            label: result for label, result in zip(tasks.keys(), results, strict=False) if isinstance(result, Exception)
        }
        if failures:
            if self._is_shutting_down:
                for label, failure in failures.items():
                    logger.debug("Suppressed %s gameover error during shutdown: %s", label, failure)
                return
            for label, failure in failures.items():
                logger.error("Engine %s gameover notification failed: %s", label, failure, exc_info=failure)
            message = ", ".join(f"{label}={type(exc).__name__}:{exc}" for label, exc in failures.items())
            first_exc = next(iter(failures.values()))
            raise RuntimeError(f"Engine gameover notification failed: {message}") from first_exc

    # --- Typed results for move processing ---------------------------------

    async def _process_move_result(
        self, board: Board, think_result: UsiThinkResultPort, engine_name: str
    ) -> _GameMoveResult:
        """
        Process the move result from engine.

        Returns:
            Typed dict indicating either game termination or next move
        """
        bestmove = think_result.bestmove

        # Engine returned no usable bestmove (no response / unparseable). Treat as a loss for the
        # side to move rather than crashing. This must come before the Move.MOVE_RESIGN/MOVE_WIN
        # comparisons, since comparing None to a Move raises TypeError.
        if bestmove is None:
            logger.warning(f"No bestmove from {engine_name}; treating as loss for the side to move")
            result = GameResult.WHITE_WIN if board.turn.is_black() else GameResult.BLACK_WIN
            return _GameOverResult(is_game_over=True, result=result)

        # Handle special moves
        if bestmove == Move.MOVE_RESIGN:
            logger.debug(f"Engine {engine_name} resigned")
            # Current player loses
            result = GameResult.WHITE_WIN if board.turn.is_black() else GameResult.BLACK_WIN
            return _GameOverResult(is_game_over=True, result=result)

        if bestmove == Move.MOVE_WIN:
            logger.debug(f"Engine {engine_name} declared win")
            # Current player wins
            result = GameResult.BLACK_WIN if board.turn.is_black() else GameResult.WHITE_WIN
            return _GameOverResult(is_game_over=True, result=result)

        # Validate normal move
        if not board.is_legal_move(bestmove):
            logger.warning(f"Illegal move from {engine_name}: {bestmove.to_usi()}")
            result = GameResult.WHITE_WIN if board.turn.is_black() else GameResult.BLACK_WIN
            return _GameOverResult(is_game_over=True, result=result)

        return _MoveContinue(is_game_over=False, move=bestmove)

    def _extract_evaluation(self, think_result: UsiThinkResultPort) -> int | None:
        """Extract evaluation value from think result.

        Returns evaluation from the engine's perspective (positive = good for engine).
        """
        if think_result.pvs:
            pv = think_result.pvs[0]
            if pv.eval is not None:
                return int(pv.eval)
        return None

    def _extract_search_statistics(
        self, think_result: UsiThinkResultPort, fallback_time_ms: int | None = None
    ) -> dict[str, int | None]:
        """Extract search statistics from think result.

        Args:
            think_result: The USI think result from engine
            fallback_time_ms: Fallback time in milliseconds if engine doesn't report time

        Returns dict with depth, seldepth, nodes, time_ms.
        """
        stats: dict[str, int | None] = {"depth": None, "seldepth": None, "nodes": None, "time_ms": None}

        # Get the last PV which should have the most recent stats
        last_pv = think_result.get_last_pv()
        if last_pv:
            stats["depth"] = last_pv.depth
            stats["seldepth"] = last_pv.seldepth
            stats["nodes"] = last_pv.nodes
            stats["time_ms"] = last_pv.time  # time is already in ms

            # Use fallback time if engine didn't report time
            if stats["time_ms"] is None and fallback_time_ms is not None:
                stats["time_ms"] = fallback_time_ms

        return stats

    async def _notify_clock_increment(
        self,
        *,
        game_id: str | None,
        ply_count: int,
        is_side_that_moved_black: bool,
        current_time_control: GameClock,
        black_time_control: GameClock,
        white_time_control: GameClock,
        pre_black_remain_ms: int,
        pre_white_remain_ms: int,
    ) -> None:
        """Notify UI about clock increment."""
        payload: _ClockIncrementPayload = {
            "type": "clock_increment",
            "game_id": game_id,
            "side": "black" if is_side_that_moved_black else "white",
            "applied_increment_ms": int(current_time_control.limits.increment_ms or 0),
            "pre_black_remain_ms": int(pre_black_remain_ms),
            "pre_white_remain_ms": int(pre_white_remain_ms),
            "black_remain_ms": int(black_time_control.active_time_left_ms()),
            "white_remain_ms": int(white_time_control.active_time_left_ms()),
            "occurred_at_ms": int(time.time() * 1000),
        }
        await self._enqueue_progress(game_id, ply_count, payload)
