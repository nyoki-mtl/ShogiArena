"""Ponder start/hit helpers for GameRunner."""

from __future__ import annotations

import logging
from dataclasses import replace

from rshogi.core import Move

from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import (
    PonderHitTimings,
    UsiThinkResultPort,
    request_from_time_controls,
)
from shogiarena._core.shared.kernel.time_control import GameClock

logger = logging.getLogger(__name__)


class GameRunnerPonderMixin:
    def _build_ponder_hit_timings(
        self,
        *,
        current_time_control: GameClock,
        enemy_time_control: GameClock,
        is_black_turn: bool,
    ) -> PonderHitTimings:
        current_limits = current_time_control.limits
        enemy_limits = enemy_time_control.limits
        request = request_from_time_controls(
            my_limits=current_limits,
            enemy_limits=enemy_limits,
            is_my_black=is_black_turn,
            my_remaining_ms=current_time_control.active_time_left_ms(),
            enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
        )
        return PonderHitTimings(
            btime=request.btime,
            wtime=request.wtime,
            binc=request.binc,
            winc=request.winc,
            byoyomi=request.byoyomi,
        )

    async def _maybe_start_ponder(
        self,
        *,
        engine: GameEnginePort,
        think_result: UsiThinkResultPort,
        is_black_turn: bool,
        initial_sfen: str,
        moves: list[Move],
        current_time_control: GameClock,
        enemy_time_control: GameClock,
    ) -> None:
        predicted = think_result.ponder
        if predicted is None:
            await engine.cancel_ponder(timeout=0.2)
            return
        ponder_moves = list(moves)
        ponder_moves.append(predicted)
        try:
            ponder_request = request_from_time_controls(
                my_limits=current_time_control.limits,
                enemy_limits=enemy_time_control.limits,
                is_my_black=is_black_turn,
                my_remaining_ms=current_time_control.active_time_left_ms(),
                enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
            )
            ponder_request = replace(ponder_request, is_ponder=True)
            await engine.start_ponder(
                sfen=initial_sfen,
                moves=tuple(ponder_moves),
                request=ponder_request,
                predicted_move=predicted,
            )
        except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to start ponder for %s: %s", engine.name, exc, exc_info=True)
            await engine.cancel_ponder(timeout=0.2)
