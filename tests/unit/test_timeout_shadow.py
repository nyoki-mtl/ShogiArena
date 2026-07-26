"""GameRunner の timeout 分岐（task 0047、0052 で証拠モデルへ更新）。

reclassification の opt-in / opt-out と、証拠が揃わない経路の fail-closed を固定する。
"""

from __future__ import annotations

from rsshogi.types import Color

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagObservation
from shogiarena._core.shared.kernel.timeout_attribution import (
    ObservationBasis,
    TimeoutAttributionDecision,
    TimeoutOrigin,
    TimeoutWindow,
)

_START_S = 100.0
_BUDGET_MS = 1000.0


class _FakeWatchdog:
    def __init__(self, lag_ms: float, *, coverage_complete: bool = True) -> None:
        self._lag_ms = lag_ms
        self._coverage_complete = coverage_complete

    def observe_loop_lag(self, start_s: float, end_s: float) -> LoopLagObservation:
        del start_s, end_s
        return LoopLagObservation(
            lag_ms_in_window=self._lag_ms,
            in_flight_lag_ms=0.0,
            is_available=True,
            coverage_complete=self._coverage_complete,
            watchdog_generation=1,
            dropped_event_count=0,
            dropped_through_s=None,
        )


def _window() -> TimeoutWindow:
    return TimeoutWindow(
        started_at_s=_START_S,
        budget_ms=_BUDGET_MS,
        deadline_s=_START_S + _BUDGET_MS / 1000.0,
        clock_mode="fixed",
    )


def _call(
    runner: GameRunner,
    *,
    window: TimeoutWindow | None,
    observed_at_s: float | None = _START_S + 5.0,
    observation_basis: str | None = ObservationBasis.LOCAL_PIPE.value,
) -> tuple[GameResult, TimeoutAttributionDecision | None]:
    holder: list[TimeoutAttributionDecision | None] = [None]
    result = runner._timeout_result_or_error(  # noqa: SLF001
        winner_color=Color.BLACK,
        decision_holder=holder,
        window=window,
        site="test",
        game_id="g1",
        observed_at_s=observed_at_s,
        observation_basis=observation_basis,
    )
    return result, holder[0]


def test_no_watchdog_stays_timeout_loss() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_timeout_reclassification(True)  # even enabled, no probe => unattributed
    result, decision = _call(runner, window=_window())
    assert result == GameResult.BLACK_WIN_BY_TIMEOUT
    assert decision is not None
    assert decision.origin is TimeoutOrigin.UNATTRIBUTED


def test_reclassification_off_keeps_loss_even_for_an_invalid_origin() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=9000.0))
    # reclassification disabled (default): still a timeout loss, but the origin is recorded.
    result, decision = _call(runner, window=_window())
    assert result == GameResult.BLACK_WIN_BY_TIMEOUT
    assert decision is not None
    assert decision.origin is TimeoutOrigin.UNKNOWN
    assert runner._timeout_shadow_counts.get(TimeoutOrigin.UNKNOWN.value) == 1  # noqa: SLF001


def test_reclassification_on_orchestrator_stall_becomes_error() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0))
    runner.set_timeout_reclassification(True)
    # deadline より前に bestmove を観測しており、その後の処理だけが遅れた（positive evidence）。
    result, decision = _call(runner, window=_window(), observed_at_s=_START_S + 0.5)
    assert result == GameResult.ERROR
    assert decision is not None
    assert decision.origin is TimeoutOrigin.ORCHESTRATOR_STALL


def test_reclassification_on_engine_deadline_stays_loss() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0))
    runner.set_timeout_reclassification(True)
    result, decision = _call(runner, window=_window())
    assert result == GameResult.BLACK_WIN_BY_TIMEOUT
    assert decision is not None
    assert decision.origin is TimeoutOrigin.ENGINE_DEADLINE


def test_missing_window_is_invalidated_rather_than_scored() -> None:
    """clock 状態が揃わない防御経路は、正常な勝敗へ倒さず無効局にする（task 0052）。"""

    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0))
    runner.set_timeout_reclassification(True)
    result, decision = _call(runner, window=None)
    assert result == GameResult.ERROR
    assert decision is not None
    assert decision.origin is TimeoutOrigin.UNKNOWN


def test_search_limits_wait_failure_is_a_transport_timeout() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0))
    runner.set_timeout_reclassification(True)
    window = TimeoutWindow(started_at_s=_START_S, budget_ms=None, deadline_s=None, clock_mode="search_limits")
    result, decision = _call(runner, window=window, observed_at_s=None, observation_basis=None)
    assert result == GameResult.ERROR
    assert decision is not None
    assert decision.origin is TimeoutOrigin.TRANSPORT_TIMEOUT


def test_coverage_loss_is_invalidated_not_scored() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0, coverage_complete=False))
    runner.set_timeout_reclassification(True)
    result, decision = _call(runner, window=_window())
    assert result == GameResult.ERROR
    assert decision is not None
    assert decision.origin is TimeoutOrigin.UNKNOWN


def test_remote_transport_observation_is_not_scored_as_an_engine_deadline() -> None:
    runner = GameRunner(progress_queue=None)
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms=0.0))
    runner.set_timeout_reclassification(True)
    result, decision = _call(
        runner,
        window=_window(),
        observation_basis=ObservationBasis.REMOTE_TRANSPORT.value,
    )
    assert result == GameResult.ERROR
    assert decision is not None
    assert decision.origin is TimeoutOrigin.UNKNOWN
