"""Progress contract tests for event handling failure cases."""

from __future__ import annotations

from shogiarena._core.contexts.game_session.application.progress.consumption import ProgressState
from shogiarena._core.contexts.game_session.application.progress.consumption_event_handlers import (
    handle_clock_increment,
    handle_clock_start,
    handle_move_progress,
)
from shogiarena._core.contexts.game_session.application.progress.events import parse_progress_event
from shogiarena._core.shared.kernel.game_results import GameResult


def _new_state() -> ProgressState:
    return ProgressState(
        num_workers=1,
        game_to_worker={},
        worker_busy=set(),
        worker_snapshots={},
    )


class TestProgressContractViolations:
    def test_move_progress_with_terminal_result_updates_snapshot(self) -> None:
        state = _new_state()
        event = parse_progress_event(
            {
                "type": "move_progress",
                "game_id": "g-1",
                "initial_sfen": "startpos",
                "move": "7g7f",
                "game_result": "BLACK_WIN",
            }
        )

        diff = handle_move_progress(
            worker_idx=0,
            current_gen=1,
            progress=event,
            state=state,
            game_id_num=1,
        )

        assert diff is not None
        snapshot = state.worker_snapshots[0]
        assert snapshot.game_result == GameResult.BLACK_WIN

    def test_clock_start_without_initial_sfen_is_ignored(self) -> None:
        state = _new_state()
        event = parse_progress_event(
            {
                "type": "clock_start",
                "game_id": "g-1",
                "black_remain_ms": 100,
                "white_remain_ms": 90,
            }
        )

        diff = handle_clock_start(
            worker_idx=0,
            current_gen=1,
            progress=event,
            state=state,
            game_id_num=1,
        )

        assert diff is None
        assert state.worker_snapshots == {}

    def test_clock_increment_without_initial_sfen_is_ignored(self) -> None:
        state = _new_state()
        event = parse_progress_event(
            {
                "type": "clock_increment",
                "game_id": "g-1",
                "black_remain_ms": 100,
                "white_remain_ms": 90,
                "occurred_at_ms": 123,
            }
        )

        diff = handle_clock_increment(
            worker_idx=0,
            current_gen=1,
            progress=event,
            state=state,
            game_id_num=1,
        )

        assert diff is None
        assert state.worker_snapshots == {}
