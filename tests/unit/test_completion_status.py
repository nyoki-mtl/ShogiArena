"""``completion_status.json`` の status / termination reason 決定表（task 0052）。

判定は純関数として固定する。とくに SPRT の正常早期終了が ``failed`` にならないこと
（review finding M2）と、未実施局の field 名が ``not_played`` であることを回帰として押さえる。
"""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.game_session.application.summary.finalize_service import (
    build_run_health_inputs,
    build_watchdog_payload,
)
from shogiarena._core.contexts.game_session.domain.run_health import (
    RunHealthInputs,
    RunTerminationReason,
    build_completion_status_payload,
    resolve_run_health_status,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.runtime_watchdog import WatchdogSummary


def _results(*, total: int, completed: int, cancelled: int = 0) -> TournamentResults:
    return TournamentResults(
        engine_stats={},
        pair_results={},
        completed_games=[],
        total_games=total,
        completed_games_count=completed,
        cancelled_games_count=cancelled,
    )


def _game(result: GameResult, *, timeout_origin: str | None = None) -> GameRecordPlayers:
    game: GameRecordPlayers = {"black_player": "a", "white_player": "b", "result": result}
    if timeout_origin is not None:
        game["timeout_origin"] = timeout_origin
    return game


@dataclass
class _SprtDecisionStub:
    value: str


@dataclass
class _SprtStatusStub:
    decision: _SprtDecisionStub


class _SprtStub:
    """``is_finished()`` と decision の双方を持つ最小 stub。"""

    def __init__(self, *, finished: bool, decision: str) -> None:
        self._finished = finished
        self._decision = decision

    def is_finished(self) -> bool:
        return self._finished

    def get_status(self) -> _SprtStatusStub:
        return _SprtStatusStub(decision=_SprtDecisionStub(value=self._decision))


class _WatchdogStub:
    def summary(self) -> WatchdogSummary:
        return WatchdogSummary(
            loop_lag_events=2,
            thread_lag_events=1,
            max_loop_lag_ms=1234.56,
            max_thread_lag_ms=300.0,
            loop_threshold_ms=200.0,
            thread_threshold_ms=200.0,
        )


def _status(
    results: TournamentResults,
    games: list[GameRecordPlayers],
    *,
    stop_reason: str | None = None,
    sprt: _SprtStub | None = None,
    watchdog: _WatchdogStub | None = None,
) -> dict[str, object]:
    inputs = build_run_health_inputs(results, games, stop_reason=stop_reason, sprt_service=sprt)  # type: ignore[arg-type]
    return dict(build_completion_status_payload(inputs, watchdog=build_watchdog_payload(watchdog)))  # type: ignore[arg-type]


# --- 正常終了 -----------------------------------------------------------------


def test_schedule_complete_without_anomalies_is_clean() -> None:
    status = _status(
        _results(total=4, completed=4),
        [_game(GameResult.DRAW_BY_MAX_PLIES) for _ in range(4)],
    )
    assert status["status"] == "clean"
    assert status["termination_reason"] == "schedule-complete"
    assert status["error_games"] == 0
    assert status["not_played"] == 0


def test_sprt_early_finish_is_clean_with_not_played_games() -> None:
    """SPRT が正常に早期終了した run は成功。予定数との差は ``not_played`` として残す。"""

    status = _status(
        _results(total=1000, completed=124, cancelled=8),
        [_game(GameResult.BLACK_WIN) for _ in range(124)],
        stop_reason="sprt-finished",
        sprt=_SprtStub(finished=True, decision="accept_h1"),
    )
    assert status["status"] == "clean"
    assert status["termination_reason"] == "sprt-finished"
    assert status["not_played"] == 868


def test_sprt_early_finish_with_error_games_is_with_anomalies() -> None:
    games = [_game(GameResult.BLACK_WIN) for _ in range(9)] + [_game(GameResult.ERROR)]
    status = _status(
        _results(total=50, completed=10),
        games,
        stop_reason="sprt-finished",
        sprt=_SprtStub(finished=True, decision="accept_h0"),
    )
    assert status["status"] == "with-anomalies"
    assert status["termination_reason"] == "sprt-finished"


def test_schedule_complete_with_error_games_is_with_anomalies() -> None:
    games = [
        _game(GameResult.DRAW_BY_MAX_PLIES),
        _game(GameResult.ERROR),
        _game(GameResult.BLACK_WIN),
        _game(GameResult.ERROR),
    ]
    status = _status(_results(total=4, completed=4), games)
    assert status["status"] == "with-anomalies"
    assert status["error_games"] == 2


# --- stop reason 文字列だけを成功証拠にしない --------------------------------


def test_sprt_stop_reason_without_decision_is_failed() -> None:
    status = _status(
        _results(total=100, completed=10),
        [_game(GameResult.BLACK_WIN) for _ in range(10)],
        stop_reason="sprt-finished",
        sprt=_SprtStub(finished=False, decision="continue"),
    )
    assert status["status"] == "failed"
    assert status["termination_reason"] == "incomplete"


def test_sprt_stop_reason_without_service_is_failed() -> None:
    status = _status(
        _results(total=100, completed=10),
        [_game(GameResult.BLACK_WIN) for _ in range(10)],
        stop_reason="sprt-finished",
        sprt=None,
    )
    assert status["status"] == "failed"


# --- controlled stop と安全装置 ----------------------------------------------


def test_user_cancellation_is_failed_with_cancelled_reason() -> None:
    status = _status(
        _results(total=10, completed=4),
        [_game(GameResult.BLACK_WIN) for _ in range(4)],
        stop_reason="cancelled",
    )
    assert status["status"] == "failed"
    assert status["termination_reason"] == "cancelled"


def test_explicit_failure_reason_wins_over_completed_counts() -> None:
    """failure/cancellation reason があれば game count にかかわらず ``failed``。"""

    status = _status(
        _results(total=4, completed=4),
        [_game(GameResult.BLACK_WIN) for _ in range(4)],
        stop_reason="cancelled",
    )
    assert status["status"] == "failed"
    assert status["not_played"] == 0


def test_timeout_burst_stop_is_failed_with_specific_reason() -> None:
    status = _status(
        _results(total=10, completed=6),
        [_game(GameResult.ERROR, timeout_origin="orchestrator_stall") for _ in range(6)],
        stop_reason="timeout-burst",
    )
    assert status["status"] == "failed"
    assert status["termination_reason"] == "timeout-burst"


def test_unknown_timeout_safety_stop_has_its_own_reason() -> None:
    status = _status(
        _results(total=10, completed=6),
        [_game(GameResult.ERROR, timeout_origin="unknown") for _ in range(6)],
        stop_reason="timeout-attribution-unknown",
    )
    assert status["termination_reason"] == "timeout-attribution-unknown"


def test_transport_timeout_safety_stop_has_its_own_reason() -> None:
    status = _status(
        _results(total=10, completed=6),
        [_game(GameResult.ERROR, timeout_origin="transport_timeout") for _ in range(6)],
        stop_reason="transport-timeout",
    )
    assert status["termination_reason"] == "transport-timeout"


def test_unrecognized_incomplete_run_is_failed() -> None:
    status = _status(
        _results(total=10, completed=6, cancelled=1),
        [_game(GameResult.DRAW_BY_MAX_PLIES) for _ in range(6)],
    )
    assert status["status"] == "failed"
    assert status["termination_reason"] == "incomplete"
    assert status["not_played"] == 3


def test_unrecognized_stop_reason_is_kept_for_diagnosis() -> None:
    status = _status(
        _results(total=10, completed=10),
        [_game(GameResult.BLACK_WIN) for _ in range(10)],
        stop_reason="openbench-stop",
    )
    assert status["termination_reason"] == "schedule-complete"
    assert status["stop_reason"] == "openbench-stop"


def test_cancelled_out_schedule_counts_as_complete() -> None:
    status = _status(
        _results(total=4, completed=2, cancelled=2),
        [_game(GameResult.DRAW_BY_MAX_PLIES), _game(GameResult.WHITE_WIN)],
    )
    assert status["status"] == "clean"
    assert status["not_played"] == 0


# --- artifact schema ---------------------------------------------------------


def test_schema_version_is_present_and_incomplete_field_is_not_published() -> None:
    status = _status(_results(total=1, completed=1), [_game(GameResult.BLACK_WIN)])
    assert status["schema_version"] == 1
    assert "not_played" in status
    assert "incomplete" not in status


def test_timeouts_are_counted_by_origin() -> None:
    games = [
        _game(GameResult.BLACK_WIN, timeout_origin="engine_deadline"),
        _game(GameResult.ERROR, timeout_origin="orchestrator_stall"),
        _game(GameResult.WHITE_WIN, timeout_origin="engine_deadline"),
        _game(GameResult.DRAW_BY_MAX_PLIES),
    ]
    status = _status(_results(total=4, completed=4), games)
    assert status["timeouts_by_origin"] == {"engine_deadline": 2, "orchestrator_stall": 1}


def test_timeouts_by_origin_is_empty_without_origin_information() -> None:
    """origin を持たない DB（additive table 以前）でもキー自体は常に置く。"""

    status = _status(
        _results(total=2, completed=2),
        [_game(GameResult.BLACK_WIN), _game(GameResult.WHITE_WIN)],
    )
    assert status["timeouts_by_origin"] == {}


def test_watchdog_summary_is_recorded_when_measured() -> None:
    status = _status(
        _results(total=1, completed=1),
        [_game(GameResult.BLACK_WIN)],
        watchdog=_WatchdogStub(),
    )
    assert status["watchdog"] == {
        "loop_lag_events": 2,
        "thread_lag_events": 1,
        "max_loop_lag_ms": 1234.6,
        "max_thread_lag_ms": 300.0,
        "loop_threshold_ms": 200.0,
        "thread_threshold_ms": 200.0,
        "dropped_loop_events": 0,
        "is_coverage_complete": True,
    }


def test_watchdog_is_null_when_not_measured() -> None:
    status = _status(_results(total=1, completed=1), [_game(GameResult.BLACK_WIN)])
    assert status["watchdog"] is None


# --- coverage 欠落は anomaly として扱う --------------------------------------


def test_unknown_timeouts_are_recorded_as_coverage_incomplete() -> None:
    """証拠が原因を確定できなかった timeout の件数を artifact に残す。"""

    games = [
        _game(GameResult.ERROR, timeout_origin="unknown"),
        _game(GameResult.ERROR, timeout_origin="unknown"),
        _game(GameResult.BLACK_WIN, timeout_origin="engine_deadline"),
    ]
    status = _status(_results(total=3, completed=3), games)
    assert status["coverage_incomplete_timeouts"] == 2
    assert status["status"] == "with-anomalies"


def test_coverage_incomplete_timeouts_make_a_complete_schedule_anomalous() -> None:
    inputs = RunHealthInputs(
        termination_reason=RunTerminationReason.SCHEDULE_COMPLETE,
        scheduled=4,
        completed=4,
        cancelled=0,
        error_games=0,
        coverage_incomplete_timeouts=1,
    )
    assert resolve_run_health_status(inputs).value == "with-anomalies"


def test_finalization_error_reason_is_failed() -> None:
    inputs = RunHealthInputs(
        termination_reason=RunTerminationReason.FINALIZATION_ERROR,
        scheduled=4,
        completed=4,
        cancelled=0,
        error_games=0,
    )
    assert resolve_run_health_status(inputs).value == "failed"
