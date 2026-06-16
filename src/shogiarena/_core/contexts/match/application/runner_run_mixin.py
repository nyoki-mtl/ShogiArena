"""Game bootstrap and record assembly helpers for GameRunner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import rshogi
from rshogi.core import Board, Move, normalize_usi_position

from shogiarena._core.contexts.match.domain.adjudication import Adjudicator
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.shared.kernel.game_results import (
    GameResult,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.time_control import (
    GameClock,
    TimeControlLimitsPort,
    coerce_time_control_limits,
    limits_to_record_time_spec,
)

from .runner_types import _OptionNameEnginePort, _ReadyStateEnginePort, _starting_ply_number

logger = logging.getLogger(__name__)


class GameRunnerRunMixin:
    time_control_limits: TimeControlLimitsPort | None
    adjudication_config: Any
    repetition_occurrences_to_draw: int
    _is_shutting_down: bool
    _engine_options_callback: Callable[[str, Any, dict[str, str] | None], None] | None
    _published_engine_options: set[str]

    _engine_io_listener_context: Any
    _game_loop: Any
    _finalize_game: Any
    _enqueue_game_result: Any

    async def run_game(
        self,
        black_engine: GameEnginePort,
        white_engine: GameEnginePort,
        initial_sfen: str = "startpos",
        game_id: str | None = None,
        black_time_control_limits: TimeControlLimitsPort | None = None,
        white_time_control_limits: TimeControlLimitsPort | None = None,
    ) -> rshogi.record.GameRecord:
        """
        Run a single game between two engines.

        Args:
            black_engine: Engine playing black (sente)
            white_engine: Engine playing white (gote)
            initial_sfen: Starting position (SFEN format or "startpos")
            game_id: Optional game identifier

        Returns:
            GameRecord object with game results
        """
        if game_id is None:
            game_id = f"game_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.debug(f"Starting game {game_id}: {black_engine.name} (Black) vs {white_engine.name} (White)")

        # Initialize game_result to handle exceptions
        game_result = GameResult.ERROR
        result_progress_emitted = [False]

        # Initialize game state with normalized SFEN
        board = Board()

        normalized_sfen = normalize_usi_position(initial_sfen)
        if normalized_sfen == "startpos":
            board.reset()
        else:
            board.set_sfen(normalized_sfen)
        initial_sfen = normalized_sfen

        start_ply_number = _starting_ply_number(initial_sfen)

        start_time = datetime.now()
        moves: list[Move] = []
        eval_values: list[int | None] = []
        # Search statistics arrays for enhanced dashboard
        nodes_values: list[int | None] = []
        depth_values: list[int | None] = []
        seldepth_values: list[int | None] = []
        move_times_ms: list[int | None] = []
        wall_times_ms: list[int | None] = []
        engine_wall_times_ms: list[int | None] = []
        latency_deltas_ms: list[int | None] = []

        # Initialize time control and adjudication (time control is required)
        black_time_control: GameClock
        white_time_control: GameClock
        adjudicator: Adjudicator | None = None

        # Prefer per-side overrides if provided; fallback to global limits
        black_limits_raw = black_time_control_limits or self.time_control_limits
        white_limits_raw = white_time_control_limits or self.time_control_limits
        if black_limits_raw is None or white_limits_raw is None:
            raise RuntimeError("Time control limits are required for both sides (provide per-side or global limits)")
        black_limits = coerce_time_control_limits(black_limits_raw)
        white_limits = coerce_time_control_limits(white_limits_raw)
        black_time_control = GameClock(black_limits)
        white_time_control = GameClock(white_limits)
        black_time_control.initialize_for_game()
        white_time_control.initialize_for_game()
        logger.debug(f"Time control initialized (B/W): {black_time_control} / {white_time_control}")

        if self.adjudication_config:
            adjudicator = Adjudicator(self.adjudication_config)
            logger.debug(f"Adjudication initialized: {self.adjudication_config}")

        with (
            self._engine_io_listener_context(
                black_engine,
                white_engine,
                game_id,
                initial_sfen,
                black_engine.name,
                white_engine.name,
            ),
        ):
            is_cancelled = False
            error: Exception | None = None
            try:
                # Prepare both engines for the game
                if not self._is_shutting_down:
                    await self._prepare_engines_for_game(black_engine, white_engine, initial_sfen)
                else:
                    raise asyncio.CancelledError()

                # Game loop
                game_result = await self._game_loop(
                    board,
                    black_engine,
                    white_engine,
                    initial_sfen,
                    start_ply_number,
                    moves,
                    eval_values,
                    nodes_values,
                    depth_values,
                    seldepth_values,
                    move_times_ms,
                    wall_times_ms,
                    engine_wall_times_ms,
                    latency_deltas_ms,
                    black_time_control=black_time_control,
                    white_time_control=white_time_control,
                    game_id=game_id,
                    adjudicator=adjudicator,
                    result_progress_emitted=result_progress_emitted,
                )

            except asyncio.CancelledError:
                # Propagate cancellation to align with orchestrator-level SIGINT handling
                is_cancelled = True
                raise
            except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
                if self._is_shutting_down:
                    # Suppress error spam on shutdown
                    game_result = GameResult.PAUSED
                else:
                    logger.exception("Game %s failed with unhandled error", game_id)
                    game_result = GameResult.ERROR
                error = exc
            finally:
                # Send gameover to both engines
                if not is_cancelled and not self._is_shutting_down:
                    await self._finalize_game(black_engine, white_engine, game_result)

        if error is not None:
            raise error

        end_time = datetime.now()

        # Send final result to progress queue (as move_progress with game_result)
        if game_result is not None and not result_progress_emitted[0]:
            await self._enqueue_game_result(
                game_id=game_id,
                moves=moves,
                result=game_result,
                initial_sfen=normalized_sfen,
                final_sfen=board.to_sfen(),
                black_name=black_engine.name,
                white_name=white_engine.name,
                start_ply_number=start_ply_number,
            )

        # Build encoded TimeControl spec strings for DB/UI
        tc_spec_black_str = limits_to_record_time_spec(black_time_control.limits)
        tc_spec_white_str = limits_to_record_time_spec(white_time_control.limits)
        record_metadata = rshogi.record.GameRecordMetadata(
            game_name=game_id,
            game_type="arena",
            black_player=black_engine.name,
            white_player=white_engine.name,
            start_date=start_time.isoformat(),
            end_date=end_time.isoformat(),
            updated_date=end_time.isoformat(),
            black_time_control=rshogi.record.TimeControl.from_spec(tc_spec_black_str),
            white_time_control=rshogi.record.TimeControl.from_spec(tc_spec_white_str),
            attributes={
                "game_name": game_id,
                "game_type": "arena",
                "updated_date": end_time.isoformat(),
            },
        )
        logger.debug(f"Game {game_id} completed: {game_result}")
        record = rshogi.record.GameRecord.from_usi_main_line(
            normalized_sfen,
            [move.to_usi() for move in moves],
            result=game_result,
            move_times_ms=move_times_ms,
            evals=eval_values,
            nodes=nodes_values,
            depths=depth_values,
            seldepths=seldepth_values,
            wall_times_ms=wall_times_ms,
            latency_deltas_ms=latency_deltas_ms,
            metadata=record_metadata,
        )
        for move_record, engine_wall_time_ms in zip(record.moves, engine_wall_times_ms, strict=False):
            engine_info = move_record.engine_info
            if engine_info is not None and engine_wall_time_ms is not None:
                engine_info.set_extra("engine_wall_time_ms", int(engine_wall_time_ms))
        return record

    async def _prepare_engines_for_game(
        self,
        black_engine: GameEnginePort,
        white_engine: GameEnginePort,
        initial_sfen: str,
    ) -> None:
        """Prepare engines for a new game."""
        if self._is_shutting_down:
            return
        if isinstance(black_engine, _ReadyStateEnginePort) and isinstance(white_engine, _ReadyStateEnginePort):
            # Two-phase barrier:
            # 1) Wait until both engines are ready (readyok observed by each engine instance).
            # 2) Start game setup for both sides (usinewgame + position) in parallel.
            await asyncio.gather(
                black_engine.prepare_ready_state(),
                white_engine.prepare_ready_state(),
            )
            await asyncio.gather(
                black_engine.prepare_new_game_position(initial_sfen=initial_sfen),
                white_engine.prepare_new_game_position(initial_sfen=initial_sfen),
            )
        else:
            await asyncio.gather(
                black_engine.prepare(initial_sfen=initial_sfen),
                white_engine.prepare(initial_sfen=initial_sfen),
            )
        self._publish_engine_options_snapshot(black_engine)
        self._publish_engine_options_snapshot(white_engine)

    def _publish_engine_options_snapshot(self, engine: GameEnginePort) -> None:
        callback = self._engine_options_callback
        if callback is None:
            return
        name = engine.name
        if isinstance(engine, _OptionNameEnginePort) and engine.options_name.strip():
            name = engine.options_name
        if not name:
            return
        try:
            options = engine.get_usi_options_snapshot()
            if not isinstance(options, Mapping):
                return
            options_serialized = to_json_object(options)
            info = engine.get_engine_info_snapshot()
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Failed to capture USI options for %s: %s", name, exc, exc_info=True)
            return
        callback(name, options_serialized, info if info else None)
        self._published_engine_options.add(name)
