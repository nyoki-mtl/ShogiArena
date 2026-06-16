"""Main game loop helpers for GameRunner."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rshogi.core import Board, Move
from rshogi.types import Color

from shogiarena._core.contexts.match.domain.adjudication import Adjudicator
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import request_from_time_controls
from shogiarena._core.shared.kernel.game_results import GameResult, timeout_win_result
from shogiarena._core.shared.kernel.ki2_notation import normalize_ki2_move_text
from shogiarena._core.shared.kernel.time_control import GameClock

from .runner_types import (
    MoveApplicationDependencies,
    MoveApplicationRequest,
    MoveApplicationStateRefs,
    RecoveredBestmoveRequest,
    RecoveredBestmoveStateRefs,
)

logger = logging.getLogger(__name__)


class GameRunnerLoopMixin:
    _is_shutting_down: bool
    adjudication_config: Any
    repetition_occurrences_to_draw: int
    _apply_move_log_threshold_ms: float

    _enqueue_clock_start: Any
    _build_ponder_hit_timings: Any
    _process_move_result: Any
    _enqueue_terminal_progress: Any
    _apply_move_common: Any
    _enqueue_move_progress: Any
    _maybe_start_ponder: Any
    _handle_recovered_bestmove: Any

    async def _game_loop(
        self,
        board: Board,
        black_engine: GameEnginePort,
        white_engine: GameEnginePort,
        initial_sfen: str,
        start_ply_number: int,
        moves: list[Move],
        eval_values: list[int | None],
        nodes_values: list[int | None],
        depth_values: list[int | None],
        seldepth_values: list[int | None],
        move_times_ms: list[int | None],
        wall_times_ms: list[int | None],
        engine_wall_times_ms: list[int | None],
        latency_deltas_ms: list[int | None],
        black_time_control: GameClock,
        white_time_control: GameClock,
        game_id: str | None = None,
        adjudicator: Adjudicator | None = None,
        result_progress_emitted: list[bool] | None = None,
    ) -> GameResult:
        """
        Main game loop.

        Returns:
            GameResult indicating the outcome
        """
        # Exit immediately if shutdown requested
        if self._is_shutting_down:
            raise asyncio.CancelledError()
        initial_ply = max(int(board.game_ply) - 1, 0)
        ply_count = initial_ply
        max_plies: int | None = None
        if adjudicator and self.adjudication_config and self.adjudication_config.is_max_plies_enabled:
            max_plies = self.adjudication_config.max_plies

        # Pre-compute time control spec strings for UI (per-side)
        time_control_black_str: str = black_time_control.limits.to_spec_str()
        time_control_white_str: str = white_time_control.limits.to_spec_str()

        while True:
            # Determine current player and engine
            if self._is_shutting_down:
                raise asyncio.CancelledError()
            is_black_turn = board.turn.is_black()
            current_engine = black_engine if is_black_turn else white_engine
            player_name = "Black" if is_black_turn else "White"

            # Get time control instances
            current_time_control: GameClock = black_time_control if is_black_turn else white_time_control
            enemy_time_control: GameClock = white_time_control if is_black_turn else black_time_control

            logger.debug(f"Ply {ply_count + 1}: {player_name} to move")

            # Check for game end conditions
            # 1. Check for checkmate (no legal moves)
            if board.is_mated():
                # In shogi, no legal moves means checkmate (current player loses)
                logger.debug(f"Game ended by checkmate - {player_name} is checkmated")
                return GameResult.WHITE_WIN if is_black_turn else GameResult.BLACK_WIN

            # 2. Check for entering king declaration win
            if board.can_declare_win():
                # Current player wins by entering king declaration
                logger.debug(f"Game ended by entering king declaration - {player_name} wins")
                return GameResult.BLACK_WIN if is_black_turn else GameResult.WHITE_WIN

            # 3. Check for time expiry
            if current_time_control.is_expired():
                # If should_allow_timeout is enabled, do not end the game on time expiry
                if current_time_control.limits.should_allow_timeout:
                    logger.debug(f"Time expired for {player_name}, but should_allow_timeout=True (continuing game)")
                else:
                    logger.debug(f"Game ended by time expiry - {player_name} loses on time")
                    winner_color = Color.WHITE if is_black_turn else Color.BLACK
                    return timeout_win_result(winner_color)

            # Build think request from time controls
            think_request = request_from_time_controls(
                my_limits=current_time_control.limits,
                enemy_limits=enemy_time_control.limits,
                is_my_black=is_black_turn,
                my_remaining_ms=current_time_control.active_time_left_ms(),
                enemy_remaining_ms=enemy_time_control.active_time_left_ms(),
            )
            logger.debug(
                "Think request for %s: %s",
                current_engine.name,
                think_request.to_command(),
            )

            last_move = moves[-1] if moves else None
            should_use_ponder = False
            ponder_timings: Any = None

            # Start move clock before any ponder synchronization so cancel_ponder
            # latency is also charged to the side to move.
            current_time_control.start_timer()
            go_start_time = time.perf_counter()
            await self._enqueue_clock_start(
                game_id=game_id,
                ply=ply_count,
                start_ply_number=start_ply_number,
                is_active_black=is_black_turn,
                black_remaining_ms=black_time_control.active_time_left_ms(),
                white_remaining_ms=white_time_control.active_time_left_ms(),
                time_control_black=time_control_black_str,
                time_control_white=time_control_white_str,
                black_limits=black_time_control.limits,
                white_limits=white_time_control.limits,
                initial_sfen=initial_sfen,
                black_name=black_engine.name,
                white_name=white_engine.name,
            )

            if current_engine.has_active_ponder():
                predicted = current_engine.active_ponder_predicted_move()
                if predicted is not None and predicted == last_move:
                    should_use_ponder = True
                    ponder_timings = self._build_ponder_hit_timings(
                        current_time_control=current_time_control,
                        enemy_time_control=enemy_time_control,
                        is_black_turn=is_black_turn,
                    )
                    logger.debug("Ponderhit attempt for %s (predicted %s)", current_engine.name, predicted.to_usi())
                else:
                    wait_timeout = current_time_control.get_timeout_for_wait()
                    wait_timeout_f = 1.0 if wait_timeout is None else float(wait_timeout)
                    cancel_timeout = min(wait_timeout_f, 1.0)
                    await current_engine.cancel_ponder(timeout=cancel_timeout)

            # Calculate timeout for wait_bestmove
            timeout_seconds = current_time_control.get_timeout_for_wait()

            # Wait for bestmove
            engine_wall_start: float | None = None
            engine_wall_time_ms: int | None = None
            try:
                if self._is_shutting_down:
                    raise asyncio.CancelledError()
                if should_use_ponder:
                    engine_wall_start = time.perf_counter()
                    ponder_result = await current_engine.ponder_hit(
                        timings=ponder_timings,
                        timeout=timeout_seconds,
                    )
                    engine_wall_time_ms = int((time.perf_counter() - engine_wall_start) * 1000)
                    if ponder_result is None:
                        logger.debug(
                            "Ponder hit returned no result, falling back to fresh go for %s", current_engine.name
                        )
                        engine_wall_start = time.perf_counter()
                        think_result = await current_engine.think(
                            sfen=initial_sfen,
                            moves=tuple(moves),
                            request=think_request,
                            timeout=timeout_seconds,
                        )
                        engine_wall_time_ms = int((time.perf_counter() - engine_wall_start) * 1000)
                    else:
                        think_result = ponder_result
                else:
                    engine_wall_start = time.perf_counter()
                    think_result = await current_engine.think(
                        sfen=initial_sfen,
                        moves=tuple(moves),
                        request=think_request,
                        timeout=timeout_seconds,
                    )
                    engine_wall_time_ms = int((time.perf_counter() - engine_wall_start) * 1000)

                # Calculate elapsed time as fallback for time_ms
                elapsed_ms = int((time.perf_counter() - go_start_time) * 1000)
                move_result = await self._process_move_result(board, think_result, current_engine.name)

                if move_result["is_game_over"]:
                    game_result_obj = move_result.get("result")
                    if not isinstance(game_result_obj, GameResult):
                        raise TypeError(f"Expected GameResult, got {type(game_result_obj).__name__}")
                    game_result = game_result_obj
                    if result_progress_emitted is not None and not result_progress_emitted[0]:
                        await self._enqueue_terminal_progress(
                            game_id=game_id,
                            ply_index=len(moves) + 1,
                            start_ply_number=start_ply_number,
                            initial_sfen=initial_sfen,
                            black_name=black_engine.name,
                            white_name=white_engine.name,
                            board_sfen=board.to_sfen(),
                            result=game_result,
                            think_result=think_result,
                            elapsed_ms=elapsed_ms,
                            engine_wall_time_ms=engine_wall_time_ms,
                        )
                        result_progress_emitted[0] = True
                    return game_result

                # Record the move value for downstream processing
                move_obj = move_result.get("move")
                if not isinstance(move_obj, Move):
                    raise TypeError(f"Expected Move, got {type(move_obj).__name__}")
                move = move_obj

                # Prepare KI2 notation BEFORE applying the move
                ki2_move = normalize_ki2_move_text(board.move32_from_move(move).to_ki2(board) or move.to_usi())
                is_side_that_moved_black = board.turn.is_black()

                # Accumulate search statistics for database storage and apply move/time updates
                apply_start = time.perf_counter()
                (
                    result_after_apply,
                    ply_count,
                    eval_value,
                    search_stats,
                    wall_time_sample,
                    engine_wall_time_sample,
                ) = await self._apply_move_common(
                    request=MoveApplicationRequest(
                        move=move,
                        think_result=think_result,
                        elapsed_ms=elapsed_ms,
                        engine_wall_time_ms=engine_wall_time_ms,
                        game_id=game_id,
                        ply_count=ply_count,
                        is_side_that_moved_black=is_side_that_moved_black,
                        repetition_occurrences_to_draw=self.repetition_occurrences_to_draw,
                    ),
                    state=MoveApplicationStateRefs(
                        board=board,
                        moves=moves,
                        eval_values=eval_values,
                        nodes_values=nodes_values,
                        depth_values=depth_values,
                        seldepth_values=seldepth_values,
                        move_times_ms=move_times_ms,
                        wall_times_ms=wall_times_ms,
                        engine_wall_times_ms=engine_wall_times_ms,
                        latency_deltas_ms=latency_deltas_ms,
                        current_time_control=current_time_control,
                        black_time_control=black_time_control,
                        white_time_control=white_time_control,
                    ),
                    dependencies=MoveApplicationDependencies(adjudicator=adjudicator),
                )
                apply_elapsed_ms = (time.perf_counter() - apply_start) * 1000.0
                if apply_elapsed_ms >= self._apply_move_log_threshold_ms:
                    logger.warning(
                        "Slow move apply: game=%s ply=%s duration_ms=%.1f", game_id, ply_count, apply_elapsed_ms
                    )
                game_finished_after_move = result_after_apply is not None
                hit_max_plies = max_plies is not None and ply_count >= max_plies

                # Report progress if queue is available. Emit before early returns so that
                # dashboards receive the terminal move even when max-plies adjudication fires.
                await self._enqueue_move_progress(
                    game_id=game_id,
                    ply_index=len(moves),
                    start_ply_number=start_ply_number,
                    initial_sfen=initial_sfen,
                    black_name=black_engine.name,
                    white_name=white_engine.name,
                    board_sfen=board.to_sfen(),
                    usi_move=move.to_usi(),
                    ki2_move=ki2_move,
                    eval_cp=eval_value,
                    depth=search_stats["depth"],
                    seldepth=search_stats["seldepth"],
                    nodes=search_stats["nodes"],
                    time_ms=search_stats["time_ms"],
                    wall_time_ms=wall_time_sample,
                    engine_wall_time_ms=engine_wall_time_sample,
                )

                # A decisive result for this move (mate, illegal, timeout, repetition win,
                # resign adjudication) takes precedence over the ply limit, even when the move
                # also reaches max_plies.
                if game_finished_after_move:
                    assert result_after_apply is not None
                    return result_after_apply

                # Enforce max plies if enabled
                if hit_max_plies:
                    logger.debug(f"Game ended by ply limit ({max_plies})")
                    return GameResult.DRAW_BY_MAX_PLIES

                await self._maybe_start_ponder(
                    engine=current_engine,
                    think_result=think_result,
                    is_black_turn=is_black_turn,
                    initial_sfen=initial_sfen,
                    moves=moves,
                    current_time_control=current_time_control,
                    enemy_time_control=enemy_time_control,
                )

            except TimeoutError:
                if engine_wall_time_ms is None and engine_wall_start is not None:
                    engine_wall_time_ms = int((time.perf_counter() - engine_wall_start) * 1000)
                if self._is_shutting_down:
                    raise asyncio.CancelledError() from None
                logger.warning(f"Engine {current_engine.name} timed out")

                if should_use_ponder:
                    recovered_think = await current_engine.cancel_ponder(timeout=timeout_seconds)
                else:
                    recovered_think = await current_engine.stop()

                if recovered_think is None:
                    if current_time_control.limits.should_allow_timeout:
                        logger.debug(
                            f"Timeout recovery failed for {player_name}, but should_allow_timeout=True (treat as draw)"
                        )
                        return GameResult.DRAW_BY_MAX_PLIES
                    logger.debug(f"Timeout recovery failed - {player_name} loses on time")
                    winner_color = Color.WHITE if is_black_turn else Color.BLACK
                    return timeout_win_result(winner_color)

                result_after_recovery, new_ply_count = await self._handle_recovered_bestmove(
                    request=RecoveredBestmoveRequest(
                        think_result=recovered_think,
                        elapsed_ms=int((time.perf_counter() - go_start_time) * 1000),
                        engine_wall_time_ms=engine_wall_time_ms,
                        current_engine_name=current_engine.name,
                        game_id=game_id,
                        ply_count=ply_count,
                        start_ply_number=start_ply_number,
                        initial_sfen=initial_sfen,
                        black_name=black_engine.name,
                        white_name=white_engine.name,
                        player_name=player_name,
                        is_black_turn=is_black_turn,
                        repetition_occurrences_to_draw=self.repetition_occurrences_to_draw,
                    ),
                    state=RecoveredBestmoveStateRefs(
                        move_state=MoveApplicationStateRefs(
                            board=board,
                            moves=moves,
                            eval_values=eval_values,
                            nodes_values=nodes_values,
                            depth_values=depth_values,
                            seldepth_values=seldepth_values,
                            move_times_ms=move_times_ms,
                            wall_times_ms=wall_times_ms,
                            engine_wall_times_ms=engine_wall_times_ms,
                            latency_deltas_ms=latency_deltas_ms,
                            current_time_control=current_time_control,
                            black_time_control=black_time_control,
                            white_time_control=white_time_control,
                        ),
                        result_progress_emitted=result_progress_emitted,
                    ),
                    dependencies=MoveApplicationDependencies(adjudicator=adjudicator),
                )
                ply_count = new_ply_count
                if result_after_recovery is not None:
                    return result_after_recovery
                continue

            except asyncio.CancelledError:
                raise

            except (OSError, RuntimeError, ValueError) as exc:
                if self._is_shutting_down:
                    logger.debug("Suppressed move error during shutdown for %s", current_engine.name)
                else:
                    logger.exception("Error during move by %s: %s", current_engine.name, exc)
                # Error results in loss for the current player
                return GameResult.WHITE_WIN if is_black_turn else GameResult.BLACK_WIN
