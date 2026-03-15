"""Move application and timeout-recovery helpers for GameRunner."""

from __future__ import annotations

import logging
import time
from typing import Any

from rshogi.core import Move
from rshogi.types import Color, RepetitionState

from shogiarena._core.shared.kernel.game_results import GameResult, timeout_win_result

from .runner_types import (
    ApplyMoveCommonResult,
    MoveApplicationDependencies,
    MoveApplicationRequest,
    MoveApplicationStateRefs,
    RecoveredBestmoveRequest,
    RecoveredBestmoveResult,
    RecoveredBestmoveStateRefs,
)

logger = logging.getLogger(__name__)


class GameRunnerMoveMixin:
    _clock_notify_log_threshold_ms: float
    _extract_evaluation: Any
    _extract_search_statistics: Any
    _process_move_result: Any
    _enqueue_terminal_progress: Any
    _notify_clock_increment: Any

    async def _handle_recovered_bestmove(
        self,
        *,
        request: RecoveredBestmoveRequest,
        state: RecoveredBestmoveStateRefs,
        dependencies: MoveApplicationDependencies,
    ) -> RecoveredBestmoveResult:
        """
        Process recovered bestmove after a timeout stop.

        Returns (game_result_or_none, new ply count). If result is None, caller continues loop.
        """
        move_state = state.move_state
        board = move_state.board
        think_result = request.think_result
        move_result = await self._process_move_result(board, think_result, request.current_engine_name)

        if move_result["is_game_over"]:
            result_obj = move_result.get("result")
            if not isinstance(result_obj, GameResult):
                raise TypeError(f"Expected GameResult, got {type(result_obj).__name__}")
            result = result_obj
            assert isinstance(result, GameResult), f"Expected GameResult, got {type(result)}"
            if state.result_progress_emitted is not None and not state.result_progress_emitted[0]:
                await self._enqueue_terminal_progress(
                    game_id=request.game_id,
                    ply_index=len(move_state.moves) + 1,
                    start_ply_number=request.start_ply_number,
                    initial_sfen=request.initial_sfen,
                    black_name=request.black_name,
                    white_name=request.white_name,
                    board_sfen=board.to_sfen(),
                    result=result,
                    think_result=think_result,
                    elapsed_ms=request.elapsed_ms,
                )
                state.result_progress_emitted[0] = True
            return RecoveredBestmoveResult(result=result, ply_count=request.ply_count)

        # Strict timeout policy: if time already expired and should_allow_timeout is False,
        # treat as loss even if a late bestmove arrived after stop.
        current_time_control = move_state.current_time_control
        if current_time_control.is_expired() and not current_time_control.limits.should_allow_timeout:
            logger.debug(
                "Time expired for %s before applying increment; strict timeout -> loss on time",
                request.player_name,
            )
            winner_color = Color.WHITE if request.is_black_turn else Color.BLACK
            return RecoveredBestmoveResult(result=timeout_win_result(winner_color), ply_count=request.ply_count)

        # Apply recovered move using common path
        move_obj = move_result.get("move")
        if not isinstance(move_obj, Move):
            raise TypeError(f"Expected Move, got {type(move_obj).__name__}")
        move = move_obj
        is_side_that_moved_black = board.turn.is_black()
        (
            result_after_apply,
            new_ply_count,
            _eval_value,
            _search_stats,
            _wall_sample,
        ) = await self._apply_move_common(
            request=MoveApplicationRequest(
                move=move,
                think_result=think_result,
                elapsed_ms=request.elapsed_ms,
                game_id=request.game_id,
                ply_count=request.ply_count,
                is_side_that_moved_black=is_side_that_moved_black,
                repetition_occurrences_to_draw=request.repetition_occurrences_to_draw,
            ),
            state=move_state,
            dependencies=dependencies,
        )
        return RecoveredBestmoveResult(result=result_after_apply, ply_count=new_ply_count)

    async def _apply_move_common(
        self,
        *,
        request: MoveApplicationRequest,
        state: MoveApplicationStateRefs,
        dependencies: MoveApplicationDependencies,
    ) -> ApplyMoveCommonResult:
        """Common post-bestmove routine used by normal and recovery paths."""
        board = state.board
        move = request.move
        think_result = request.think_result
        elapsed_ms = request.elapsed_ms
        game_id = request.game_id
        ply_count = request.ply_count
        is_side_that_moved_black = request.is_side_that_moved_black

        # Record the move and statistics
        state.moves.append(move)
        eval_value = self._extract_evaluation(think_result)
        state.eval_values.append(eval_value)
        search_stats = self._extract_search_statistics(think_result, elapsed_ms)
        state.nodes_values.append(search_stats["nodes"])
        state.depth_values.append(search_stats["depth"])
        state.seldepth_values.append(search_stats["seldepth"])
        state.move_times_ms.append(search_stats["time_ms"])
        wall_sample: int | None = int(elapsed_ms)
        state.wall_times_ms.append(wall_sample)
        state.latency_deltas_ms.append(None)

        # Apply move
        board.apply_move(move)
        ply_count += 1
        # Repetition detection using rshogi native API.
        # is_repetition(threshold) fires when repetition_counter >= threshold,
        # where threshold = repetition_occurrences_to_draw - 1 maps occurrences to the
        # internal counter (e.g. 2 occurrences → threshold 1, 4 occurrences → threshold 3).
        # When threshold >= 3 (official 4-occurrence rule), repetition_state() also fires
        # and provides WIN/LOSE/DRAW detail for perpetual check adjudication.
        repetition_result: GameResult | None = None
        if board.is_repetition(request.repetition_occurrences_to_draw - 1):
            if request.repetition_occurrences_to_draw >= 4:
                repetition_state = board.repetition_state()
                if repetition_state == RepetitionState.DRAW:
                    repetition_result = GameResult.DRAW_BY_REPETITION
                elif repetition_state == RepetitionState.WIN:
                    # RepetitionState.WIN means the current side to move (opponent of the mover) wins.
                    repetition_result = GameResult.WHITE_WIN if is_side_that_moved_black else GameResult.BLACK_WIN
                elif repetition_state == RepetitionState.LOSE:
                    repetition_result = GameResult.BLACK_WIN if is_side_that_moved_black else GameResult.WHITE_WIN
                # SUPERIOR/INFERIOR: not a forced result, let the game continue
            else:
                repetition_result = GameResult.DRAW_BY_REPETITION

        # Check immediate game termination caused by the move (e.g., checkmate)
        if board.is_mated():
            winner_result = GameResult.BLACK_WIN if is_side_that_moved_black else GameResult.WHITE_WIN
            return ApplyMoveCommonResult(
                result=winner_result,
                ply_count=ply_count,
                eval_value=eval_value,
                search_stats=search_stats,
                wall_time_ms=wall_sample,
            )

        # Update time control and notify
        pre_black_remain = state.black_time_control.active_time_left_ms()
        pre_white_remain = state.white_time_control.active_time_left_ms()
        state.current_time_control.update_after_move(should_apply_increment=True)
        notify_start = time.perf_counter()
        await self._notify_clock_increment(
            game_id=game_id,
            ply_count=ply_count,
            is_side_that_moved_black=is_side_that_moved_black,
            current_time_control=state.current_time_control,
            black_time_control=state.black_time_control,
            white_time_control=state.white_time_control,
            pre_black_remain_ms=pre_black_remain,
            pre_white_remain_ms=pre_white_remain,
        )
        notify_elapsed_ms = (time.perf_counter() - notify_start) * 1000.0
        if notify_elapsed_ms >= self._clock_notify_log_threshold_ms:
            print(
                f"[clock-notify] game={game_id} ply={ply_count} duration_ms={notify_elapsed_ms:.1f}",
                flush=True,
            )
        if state.current_time_control.is_expired() and not state.current_time_control.limits.should_allow_timeout:
            logger.debug("Time expired after move; strict timeout -> loss on time")
            winner_color = Color.WHITE if is_side_that_moved_black else Color.BLACK
            return ApplyMoveCommonResult(
                result=timeout_win_result(winner_color),
                ply_count=ply_count,
                eval_value=eval_value,
                search_stats=search_stats,
                wall_time_ms=wall_sample,
            )

        # Adjudication
        if dependencies.adjudicator:
            pv_info = think_result.pvs[0] if think_result.pvs else None
            eval_cp = pv_info.eval if pv_info else None
            score_type = "cp" if pv_info and pv_info.eval is not None else None
            adjudication_result = dependencies.adjudicator.update(
                eval_cp=eval_cp,
                score_type=score_type,
                ply=ply_count,
                is_side_to_move_black=is_side_that_moved_black,
            )
            if adjudication_result:
                logger.debug(f"Game adjudicated: {adjudication_result}")
                return ApplyMoveCommonResult(
                    result=adjudication_result,
                    ply_count=ply_count,
                    eval_value=eval_value,
                    search_stats=search_stats,
                    wall_time_ms=wall_sample,
                )
        return ApplyMoveCommonResult(
            result=repetition_result,
            ply_count=ply_count,
            eval_value=eval_value,
            search_stats=search_stats,
            wall_time_ms=wall_sample,
        )
