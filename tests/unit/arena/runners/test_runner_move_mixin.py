from __future__ import annotations

from types import SimpleNamespace

import pytest
from rsshogi.core import Board, Move

from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.contexts.match.application.runner_move_mixin import GameRunnerMoveMixin
from shogiarena._core.contexts.match.application.runner_types import (
    MoveApplicationDependencies,
    MoveApplicationRequest,
    MoveApplicationStateRefs,
    RecoveredBestmoveRequest,
    RecoveredBestmoveStateRefs,
)
from shogiarena._core.contexts.match.domain.adjudication import AdjudicationConfig, Adjudicator
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiEvalValue
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.game_results import timeout_win_result as _timeout_win_result
from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutAttributionDecision as _TimeoutAttributionDecision
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin as _TimeoutOrigin


def _first_legal_move(board: Board) -> Move:
    return next(iter(board.legal_moves_move32())).to_move()


def _build_clock() -> GameClock:
    clock = GameClock(TimeControlLimits(time_ms=1000, increment_ms=0))
    clock.initialize_for_game()
    return clock


def _build_move_state(board: Board) -> MoveApplicationStateRefs:
    black_time_control = _build_clock()
    white_time_control = _build_clock()
    current_time_control = black_time_control if board.turn.is_black() else white_time_control
    current_time_control.start_timer()
    return MoveApplicationStateRefs(
        board=board,
        moves=[],
        eval_values=[],
        nodes_values=[],
        depth_values=[],
        seldepth_values=[],
        move_times_ms=[],
        wall_times_ms=[],
        engine_wall_times_ms=[],
        move_sources=[],
        latency_deltas_ms=[],
        current_time_control=current_time_control,
        black_time_control=black_time_control,
        white_time_control=white_time_control,
    )


class _RunnerMoveHarness(GameRunnerMoveMixin):
    def __init__(self, *, move_result: dict[str, object]) -> None:
        self._clock_notify_log_threshold_ms = 999999.0
        self._move_result = move_result
        self.notify_calls: list[dict[str, object]] = []
        self.terminal_events: list[dict[str, object]] = []
        self.timeout_calls: list[dict[str, object]] = []

    def _extract_evaluation(self, think_result: object) -> int | None:
        del think_result
        return 42

    def _extract_search_statistics(self, think_result: object, elapsed_ms: int) -> dict[str, int | None]:
        del think_result
        return {
            "nodes": 100,
            "depth": 8,
            "seldepth": 12,
            "time_ms": elapsed_ms,
        }

    async def _process_move_result(self, board: Board, think_result: object, engine_name: str) -> dict[str, object]:
        del board, think_result, engine_name
        return self._move_result

    async def _enqueue_terminal_progress(self, **payload: object) -> None:
        self.terminal_events.append(dict(payload))

    async def _notify_clock_increment(self, **payload: object) -> None:
        self.notify_calls.append(dict(payload))

    def _timeout_result_or_error(
        self, *, winner_color: object, decision_holder: list[object], site: str = "", **_kw: object
    ) -> object:
        # Test double: no watchdog, so timeouts stay a loss on time (legacy behavior).
        self.timeout_calls.append({"site": site, **_kw})
        decision_holder[0] = _TimeoutAttributionDecision(
            origin=_TimeoutOrigin.UNATTRIBUTED, site=site, reason="attribution-disabled"
        )
        return _timeout_win_result(winner_color)  # type: ignore[arg-type]


def _resign_adjudicator() -> Adjudicator:
    return Adjudicator(
        AdjudicationConfig(
            is_resign_enabled=True,
            resign_score_cp=800,
            resign_move_count=1,
            is_resign_two_sided=False,
            is_max_plies_enabled=False,
        )
    )


async def _adjudicate_white_move(eval_value: int) -> GameResult | None:
    """Apply a white move whose engine eval (moving-side perspective) is `eval_value`.

    Accepts a plain ``int`` as well as ``UsiEvalValue`` to exercise both engine-adapter shapes.
    """
    board = Board()
    board.apply_move(_first_legal_move(board))  # black moves -> white to move
    white_move = _first_legal_move(board)
    state = _build_move_state(board)
    harness = _RunnerMoveHarness(move_result={"is_game_over": False, "move": white_move})
    think_result = SimpleNamespace(pvs=[SimpleNamespace(eval=eval_value)])
    result = await harness._apply_move_common(
        request=MoveApplicationRequest(
            move=white_move,
            think_result=think_result,
            elapsed_ms=40,
            engine_wall_time_ms=33,
            move_source="search",
            game_id="g1",
            ply_count=1,
            is_side_that_moved_black=False,
            repetition_occurrences_to_draw=4,
        ),
        state=state,
        dependencies=MoveApplicationDependencies(adjudicator=_resign_adjudicator()),
    )
    return result.result


@pytest.mark.asyncio
async def test_resign_adjudication_does_not_reverse_winning_white() -> None:
    # White just moved and is winning by +900cp (moving-side perspective). The winner must NOT
    # be resigned. Regression for the eval-perspective bug that reversed white-to-move results.
    result = await _adjudicate_white_move(UsiEvalValue(900))
    assert result is None


@pytest.mark.asyncio
async def test_resign_adjudication_resigns_losing_white() -> None:
    # White just moved and is losing by -900cp (moving-side perspective) -> white resigns.
    result = await _adjudicate_white_move(UsiEvalValue(-900))
    assert result == GameResult.BLACK_WIN


@pytest.mark.asyncio
async def test_mate_score_adjudicates_white_mate_immediately() -> None:
    # White just moved with a mate score (white is mating) -> WHITE_WIN via the mate branch.
    # Without the score_type="mate" wiring this would fall through the cp path and not fire.
    result = await _adjudicate_white_move(UsiEvalValue.mate_in_ply(1))
    assert result == GameResult.WHITE_WIN


@pytest.mark.asyncio
async def test_resign_adjudication_handles_plain_int_eval() -> None:
    # The port contract types pv.eval as a plain int; adjudication must not crash on a
    # non-UsiEvalValue value (no is_mate_score method). Plain int is treated as a cp score.
    result = await _adjudicate_white_move(900)  # white winning -> not resigned
    assert result is None


@pytest.mark.asyncio
async def test_process_move_result_treats_missing_bestmove_as_loss() -> None:
    # An engine that returns no bestmove must lose (side to move), not crash the game task
    # with an AssertionError.
    harness = object.__new__(GameRunnerFinalizeMixin)
    board = Board()  # black to move
    think_result = SimpleNamespace(bestmove=None)
    result = await harness._process_move_result(board, think_result, "engine-a")
    assert result["is_game_over"] is True
    assert result["result"] == GameResult.WHITE_WIN


def test_extract_move_source_prefers_book_info_string() -> None:
    harness = object.__new__(GameRunnerFinalizeMixin)
    think_result = SimpleNamespace(info_strings=("hit book move",), get_last_pv=lambda: None)

    assert harness._extract_move_source(think_result) == "book"


def test_extract_move_source_ignores_out_of_book_info_string() -> None:
    harness = object.__new__(GameRunnerFinalizeMixin)
    think_result = SimpleNamespace(info_strings=("out of book",), get_last_pv=lambda: None)

    assert harness._extract_move_source(think_result) == "unknown"


def test_extract_move_source_uses_search_stats() -> None:
    harness = object.__new__(GameRunnerFinalizeMixin)
    think_result = SimpleNamespace(
        info_strings=(),
        get_last_pv=lambda: SimpleNamespace(depth=1, seldepth=None, nodes=None, time=None, eval=None),
    )

    assert harness._extract_move_source(think_result) == "search"


def test_extract_move_source_unknown_without_observable_source() -> None:
    harness = object.__new__(GameRunnerFinalizeMixin)
    think_result = SimpleNamespace(info_strings=(), get_last_pv=lambda: None)

    assert harness._extract_move_source(think_result) == "unknown"


@pytest.mark.asyncio
async def test_handle_recovered_bestmove_emits_terminal_progress_once() -> None:
    harness = _RunnerMoveHarness(move_result={"is_game_over": True, "result": GameResult.BLACK_WIN})
    board = Board()
    state = RecoveredBestmoveStateRefs(move_state=_build_move_state(board), result_progress_emitted=[False])

    result = await harness._handle_recovered_bestmove(
        request=RecoveredBestmoveRequest(
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=25,
            engine_wall_time_ms=20,
            move_source="unknown",
            current_engine_name="engine-a",
            game_id="g1",
            ply_count=0,
            start_ply_number=1,
            initial_sfen=board.to_sfen(),
            black_name="black",
            white_name="white",
            player_name="Black",
            is_black_turn=True,
            repetition_occurrences_to_draw=4,
        ),
        state=state,
        dependencies=MoveApplicationDependencies(adjudicator=None),
    )

    assert result.result == GameResult.BLACK_WIN
    assert state.result_progress_emitted == [True]
    assert len(harness.terminal_events) == 1
    assert harness.terminal_events[0]["elapsed_ms"] == 25
    assert harness.terminal_events[0]["engine_wall_time_ms"] == 20


@pytest.mark.asyncio
async def test_handle_recovered_bestmove_returns_timeout_loss_when_clock_already_expired() -> None:
    board = Board()
    move_state = _build_move_state(board)
    move_state.current_time_control._is_expired = True
    harness = _RunnerMoveHarness(move_result={"is_game_over": False, "move": _first_legal_move(board)})

    result = await harness._handle_recovered_bestmove(
        request=RecoveredBestmoveRequest(
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=30,
            engine_wall_time_ms=28,
            move_source="unknown",
            current_engine_name="engine-a",
            game_id="g1",
            ply_count=0,
            start_ply_number=1,
            initial_sfen=board.to_sfen(),
            black_name="black",
            white_name="white",
            player_name="Black",
            is_black_turn=True,
            repetition_occurrences_to_draw=4,
        ),
        state=RecoveredBestmoveStateRefs(move_state=move_state),
        dependencies=MoveApplicationDependencies(adjudicator=None),
    )

    assert result.result == GameResult.WHITE_WIN_BY_TIMEOUT
    assert move_state.moves == []


@pytest.mark.asyncio
async def test_apply_move_common_updates_state_via_object_parameters() -> None:
    board = Board()
    move = _first_legal_move(board)
    state = _build_move_state(board)
    harness = _RunnerMoveHarness(move_result={"is_game_over": False, "move": move})

    result = await harness._apply_move_common(
        request=MoveApplicationRequest(
            move=move,
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=40,
            engine_wall_time_ms=31,
            move_source="unknown",
            game_id="g1",
            ply_count=0,
            is_side_that_moved_black=True,
            repetition_occurrences_to_draw=4,
        ),
        state=state,
        dependencies=MoveApplicationDependencies(adjudicator=None),
    )

    assert result.result is None
    assert result.ply_count == 1
    assert state.moves == [move]
    assert state.eval_values == [42]
    assert state.nodes_values == [100]
    assert state.depth_values == [8]
    assert state.seldepth_values == [12]
    assert state.move_times_ms == [40]
    assert state.wall_times_ms == [40]
    assert state.engine_wall_times_ms == [31]
    assert state.move_sources == ["unknown"]
    assert state.latency_deltas_ms == [None]
    assert len(harness.notify_calls) == 1


@pytest.mark.asyncio
async def test_handle_recovered_bestmove_applies_late_bestmove_with_object_state() -> None:
    board = Board()
    move = _first_legal_move(board)
    move_state = _build_move_state(board)
    harness = _RunnerMoveHarness(move_result={"is_game_over": False, "move": move})

    result = await harness._handle_recovered_bestmove(
        request=RecoveredBestmoveRequest(
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=35,
            engine_wall_time_ms=29,
            move_source="unknown",
            current_engine_name="engine-a",
            game_id="g1",
            ply_count=0,
            start_ply_number=1,
            initial_sfen=board.to_sfen(),
            black_name="black",
            white_name="white",
            player_name="Black",
            is_black_turn=True,
            repetition_occurrences_to_draw=4,
        ),
        state=RecoveredBestmoveStateRefs(move_state=move_state),
        dependencies=MoveApplicationDependencies(adjudicator=None),
    )

    assert result.result is None
    assert result.ply_count == 1
    assert move_state.moves == [move]
    assert move_state.eval_values == [42]


@pytest.mark.asyncio
async def test_handle_recovered_bestmove_preserves_timeout_observation_until_expiry_classification() -> None:
    """recovery request の local-pipe 観測を共通の expiry 判定まで失わないこと。"""

    board = Board()
    move = _first_legal_move(board)
    move_state = _build_move_state(board)
    assert move_state.current_time_control.last_move_start_time is not None
    move_state.current_time_control.last_move_start_time -= 2.0
    harness = _RunnerMoveHarness(move_result={"is_game_over": False, "move": move})

    result = await harness._handle_recovered_bestmove(
        request=RecoveredBestmoveRequest(
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=2_000,
            engine_wall_time_ms=1_950,
            move_source="search",
            current_engine_name="engine-a",
            game_id="recovered-timeout",
            ply_count=0,
            start_ply_number=1,
            initial_sfen=board.to_sfen(),
            black_name="black",
            white_name="white",
            player_name="Black",
            is_black_turn=True,
            repetition_occurrences_to_draw=4,
            observed_at_s=123.456,
            observation_basis="local_pipe",
        ),
        state=RecoveredBestmoveStateRefs(move_state=move_state),
        dependencies=MoveApplicationDependencies(adjudicator=None),
    )

    assert result.result == GameResult.WHITE_WIN_BY_TIMEOUT
    assert harness.timeout_calls[-1]["site"] == "update_after_move_expired"
    assert harness.timeout_calls[-1]["observed_at_s"] == 123.456
    assert harness.timeout_calls[-1]["observation_basis"] == "local_pipe"
