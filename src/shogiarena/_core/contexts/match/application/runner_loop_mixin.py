"""Main game loop helpers for GameRunner."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rsshogi.core import Board, Move
from rsshogi.types import Color

from shogiarena._core.contexts.match.domain.adjudication import Adjudicator
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort
from shogiarena._core.contexts.match.ports.usi_think_ports import (
    BestmoveObservationCapabilityPort,
    ObservedBestmovePort,
    request_from_time_controls,
)
from shogiarena._core.shared.kernel.engine_errors import UsiHandshakeTimeoutError
from shogiarena._core.shared.kernel.game_results import GameResult, timeout_win_result
from shogiarena._core.shared.kernel.ki2_notation import normalize_ki2_move_text
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagProbePort
from shogiarena._core.shared.kernel.time_control import GameClock
from shogiarena._core.shared.kernel.timeout_attribution import (
    DeliveryCoverage,
    ObservationBasis,
    TimeoutAttributionDecision,
    TimeoutEvidence,
    TimeoutWindow,
    classify_timeout_evidence,
)

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
    _runtime_watchdog: LoopLagProbePort | None
    _timeout_shadow_counts: dict[str, int]
    _timeout_reclassification_enabled: bool

    _enqueue_clock_start: Any
    _build_ponder_hit_timings: Any
    _process_move_result: Any
    _extract_move_source: Any
    _enqueue_terminal_progress: Any
    _apply_move_common: Any
    _enqueue_move_progress: Any
    _maybe_start_ponder: Any
    _handle_recovered_bestmove: Any

    @staticmethod
    def _delivery_coverage(basis: str | None) -> DeliveryCoverage | None:
        """観測 basis から delivery coverage を決める。

        watchdog が coverage できるのはローカル pipe の読み出しまで。remote transport や
        third-party port は同等の保証が無いので、deadline 後の観測・未観測を engine 起因の
        証拠に使わない（``unknown`` へ倒す）。
        """
        if basis is None:
            return None
        try:
            observation_basis = ObservationBasis(basis)
        except ValueError:
            return None
        return DeliveryCoverage(
            basis=observation_basis,
            is_covered=observation_basis is ObservationBasis.LOCAL_PIPE,
        )

    def _classify_timeout_at(
        self,
        *,
        window: TimeoutWindow | None,
        site: str,
        observed_at_s: float | None,
        observation_basis: str | None,
        is_transport_failure: bool = False,
    ) -> TimeoutAttributionDecision:
        """clock deadline、最早観測時刻、watchdog coverage から timeout origin を判定する。

        lag を測る窓は ``[window 開始, 観測上界]`` とする。``bestmove`` を観測した後に起きた
        停滞は、その観測が遅れた理由を説明できないため因果判定に使わない（task 0052 / H1）。
        """
        probe = self._runtime_watchdog
        now = time.perf_counter()
        # watchdog が注入されていない run（SPSA / 単体テスト / 別セッション）は判定材料が無い。
        # 従来どおり ``unattributed`` として時間切れ負けのまま扱う。
        is_enabled = probe is not None
        coverage = None
        if probe is not None and window is not None and window.deadline_s is not None:
            end_s = observed_at_s if observed_at_s is not None else now
            coverage = probe.observe_loop_lag(window.started_at_s, end_s)
        evidence = TimeoutEvidence(
            site=site,
            detected_at_s=now,
            is_attribution_enabled=is_enabled,
            window=window,
            bestmove_observed_at_s=observed_at_s,
            delivery=self._delivery_coverage(observation_basis),
            coverage=coverage,
            is_transport_failure=is_transport_failure,
        )
        return classify_timeout_evidence(evidence)

    def _timeout_result_or_error(
        self,
        *,
        winner_color: Color,
        decision_holder: list[TimeoutAttributionDecision | None],
        window: TimeoutWindow | None,
        site: str,
        game_id: str | None,
        observed_at_s: float | None = None,
        observation_basis: str | None = None,
        is_transport_failure: bool = False,
    ) -> GameResult:
        """timeout を分類し、無効 origin なら ``GameResult.ERROR``、それ以外は時間切れ負けを返す。

        reclassification が opt-in OFF のときは常に時間切れ負け（origin は shadow count のみ）。
        """
        decision = self._classify_timeout_at(
            window=window,
            site=site,
            observed_at_s=observed_at_s,
            observation_basis=observation_basis,
            is_transport_failure=is_transport_failure,
        )
        decision_holder[0] = decision
        origin = decision.origin
        self._timeout_shadow_counts[origin.value] = self._timeout_shadow_counts.get(origin.value, 0) + 1
        if self._timeout_reclassification_enabled and decision.is_invalid:
            logger.warning(
                "Invalidating timeout: game=%s site=%s origin=%s reason=%s overshoot_ms=%s lag_ms=%s",
                game_id,
                site,
                origin.value,
                decision.reason,
                decision.overshoot_ms,
                decision.lag_ms_in_window,
            )
            return GameResult.ERROR
        if decision.is_invalid:
            logger.warning(
                "Timeout classified as invalid but reclassification is off: game=%s site=%s origin=%s reason=%s",
                game_id,
                site,
                origin.value,
                decision.reason,
            )
        return timeout_win_result(winner_color)

    @staticmethod
    def _observation_of(think_result: object) -> tuple[float | None, str | None]:
        """think result が運ぶ ``bestmove`` 観測時刻と basis を取り出す（無ければ ``None``）。"""
        if isinstance(think_result, ObservedBestmovePort):
            return think_result.observed_at_s, think_result.observation_basis
        return None, None

    @staticmethod
    def _engine_observation_basis(engine: object) -> str | None:
        """``bestmove`` 未観測時に、そもそも観測できる経路だったかを engine から得る。"""
        if isinstance(engine, BestmoveObservationCapabilityPort):
            return engine.bestmove_observation_basis()
        return None

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
        move_sources: list[str | None],
        latency_deltas_ms: list[int | None],
        black_time_control: GameClock,
        white_time_control: GameClock,
        game_id: str | None = None,
        adjudicator: Adjudicator | None = None,
        result_progress_emitted: list[bool] | None = None,
        timeout_decision_holder: list[TimeoutAttributionDecision | None] | None = None,
    ) -> GameResult:
        """
        Main game loop.

        Returns:
            GameResult indicating the outcome
        """
        # Exit immediately if shutdown requested
        if self._is_shutting_down:
            raise asyncio.CancelledError()
        # Per-game out-param: the timeout attribution decision, if any (task 0047). The runner is
        # shared across workers, so this must not live on ``self``.
        decision_holder: list[TimeoutAttributionDecision | None] = (
            timeout_decision_holder if timeout_decision_holder is not None else [None]
        )
        # 最後に観測した ``bestmove`` の上界時刻。手が進むたびに更新し、次の手の証拠に流用しない。
        last_observed_at_s: float | None = None
        last_observation_basis: str | None = None
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
                    return self._timeout_result_or_error(
                        winner_color=winner_color,
                        decision_holder=decision_holder,
                        window=current_time_control.last_timeout_window,
                        site="loop_top_expired",
                        game_id=game_id,
                        observed_at_s=last_observed_at_s,
                        observation_basis=last_observation_basis or self._engine_observation_basis(current_engine),
                    )

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
                # この手の ``bestmove`` 観測。前の手の値を流用しないよう毎手上書きする。
                last_observed_at_s, last_observation_basis = self._observation_of(think_result)
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
                move_source = self._extract_move_source(think_result)

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
                        move_source=move_source,
                        game_id=game_id,
                        ply_count=ply_count,
                        is_side_that_moved_black=is_side_that_moved_black,
                        repetition_occurrences_to_draw=self.repetition_occurrences_to_draw,
                        observed_at_s=last_observed_at_s,
                        observation_basis=last_observation_basis,
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
                        move_sources=move_sources,
                        latency_deltas_ms=latency_deltas_ms,
                        current_time_control=current_time_control,
                        black_time_control=black_time_control,
                        white_time_control=white_time_control,
                        timeout_decision_holder=decision_holder,
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

            except UsiHandshakeTimeoutError:
                # A handshake stall happens before the engine is asked to think, so there is no
                # thinking time to exceed. Scoring it as a loss on time misattributes an
                # orchestrator or transport stall to the engine, and produces 0-move "timeouts".
                if self._is_shutting_down:
                    raise asyncio.CancelledError() from None
                logger.warning(
                    "Engine %s handshake timed out before go; recording as error, not a loss on time",
                    current_engine.name,
                )
                raise

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
                    return self._timeout_result_or_error(
                        winner_color=winner_color,
                        decision_holder=decision_holder,
                        # 進行中の手の deadline snapshot（wait timeout の orchestration slack は含まない）。
                        window=current_time_control.current_timeout_window,
                        site="wait_for_timeout",
                        game_id=game_id,
                        # bestmove は観測できていない。経路が観測可能だったかだけを engine から得る。
                        observed_at_s=None,
                        observation_basis=self._engine_observation_basis(current_engine),
                    )

                recovered_observed_at_s, recovered_observation_basis = self._observation_of(recovered_think)
                last_observed_at_s, last_observation_basis = recovered_observed_at_s, recovered_observation_basis
                result_after_recovery, new_ply_count = await self._handle_recovered_bestmove(
                    request=RecoveredBestmoveRequest(
                        think_result=recovered_think,
                        elapsed_ms=int((time.perf_counter() - go_start_time) * 1000),
                        engine_wall_time_ms=engine_wall_time_ms,
                        move_source=self._extract_move_source(recovered_think),
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
                        observed_at_s=recovered_observed_at_s,
                        observation_basis=recovered_observation_basis,
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
                            move_sources=move_sources,
                            latency_deltas_ms=latency_deltas_ms,
                            current_time_control=current_time_control,
                            black_time_control=black_time_control,
                            white_time_control=white_time_control,
                            timeout_decision_holder=decision_holder,
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
                    # A stop-induced interruption is not a played game. Returning a decisive result
                    # here would let the shutdown decide the outcome, and (once terminal records are
                    # always persisted) would finalize the game so resume never replays it.
                    # PAUSED keeps it non-terminal and resumable (task 0052 / Decision 8).
                    logger.debug("Interrupted move during shutdown for %s; pausing game", current_engine.name)
                    return GameResult.PAUSED
                logger.exception("Error during move by %s: %s", current_engine.name, exc)
                # Error results in loss for the current player
                return GameResult.WHITE_WIN if is_black_turn else GameResult.BLACK_WIN
