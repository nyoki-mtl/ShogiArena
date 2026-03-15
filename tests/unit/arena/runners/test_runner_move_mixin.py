from __future__ import annotations

from types import SimpleNamespace

import pytest
from rshogi.core import Board, Move

from shogiarena._core.contexts.match.application.runner_move_mixin import GameRunnerMoveMixin
from shogiarena._core.contexts.match.application.runner_types import (
    MoveApplicationDependencies,
    MoveApplicationRequest,
    MoveApplicationStateRefs,
    RecoveredBestmoveRequest,
    RecoveredBestmoveStateRefs,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits


def _first_legal_move(board: Board) -> Move:
    return next(iter(board.legal_moves_full())).to_move()


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


@pytest.mark.asyncio
async def test_handle_recovered_bestmove_emits_terminal_progress_once() -> None:
    harness = _RunnerMoveHarness(move_result={"is_game_over": True, "result": GameResult.BLACK_WIN})
    board = Board()
    state = RecoveredBestmoveStateRefs(move_state=_build_move_state(board), result_progress_emitted=[False])

    result = await harness._handle_recovered_bestmove(
        request=RecoveredBestmoveRequest(
            think_result=SimpleNamespace(pvs=[]),
            elapsed_ms=25,
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
