"""GameRunner の timeout 分岐（task 0047、0052 で証拠モデルへ更新）。

reclassification の opt-in / opt-out と、証拠が揃わない経路の fail-closed を固定する。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from rsshogi.core import Move
from rsshogi.types import Color

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort, InfoHandler
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkResult
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagObservation
from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits
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


@pytest.mark.parametrize(
    ("observed_at_s", "expected"),
    [
        (_START_S + 0.5, GameResult.BLACK_WIN_BY_DECLARATION),
        (_START_S + 1.1, GameResult.WHITE_WIN_BY_TIMEOUT),
    ],
)
def test_declaration_uses_bestmove_observation_for_deadline(observed_at_s: float, expected: GameResult) -> None:
    clock = GameClock(TimeControlLimits(fixed_time_ms=1000, expiry_margin_ms=0))
    clock.current_timeout_window = _window()
    holder: list[TimeoutAttributionDecision | None] = [None]
    runner = GameRunner(progress_queue=None)

    assert (
        runner._adjudicate_declaration_timeout(
            GameResult.BLACK_WIN_BY_DECLARATION,
            clock=clock,
            decision_holder=holder,
            game_id="g1",
            observed_at_s=observed_at_s,
            observation_basis=ObservationBasis.LOCAL_PIPE.value,
        )
        == expected
    )
    assert (holder[0] is None) == expected.is_win_by_declaration()


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


class _SlowTimeoutEngine(GameEnginePort):
    """Engine that consumes ``delay_s`` then times out (never returns a bestmove)."""

    def __init__(self, name: str, delay_s: float) -> None:
        self._name = name
        self._delay_s = delay_s

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    async def prepare(self, *, initial_sfen: str) -> None:
        return None

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        await asyncio.sleep(self._delay_s)
        raise TimeoutError("no bestmove")

    async def think_mate(self, **kwargs: object) -> UsiThinkResult:
        raise NotImplementedError

    async def analyze(self, **kwargs: object) -> UsiThinkResult:
        raise NotImplementedError

    async def notify_gameover(self, result: GameResult) -> None:
        return None

    async def stop(self) -> UsiThinkResult | None:
        return None

    async def shutdown(self) -> None:
        return None

    async def start_ponder(self, **kwargs: object) -> None:
        return None

    async def ponder_hit(self, **kwargs: object) -> UsiThinkResult | None:
        return None

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        return None

    def has_active_ponder(self) -> bool:
        return False

    def active_ponder_predicted_move(self) -> Move | None:
        return None

    def bestmove_observation_basis(self) -> str | None:
        return ObservationBasis.LOCAL_PIPE.value


async def _run_timeout_game(
    limits: TimeControlLimits,
    *,
    lag_ms: float,
    reclassify: bool,
    coverage_complete: bool = True,
    game_id: str,
):  # type: ignore[no-untyped-def]
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=None,
        repetition_occurrences_to_draw=4,
    )
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms, coverage_complete=coverage_complete))
    runner.set_timeout_reclassification(reclassify)
    black = _SlowTimeoutEngine("black", delay_s=0.02)
    white = _SlowTimeoutEngine("white", delay_s=0.02)
    return await runner.run_game(
        black_engine=black,
        white_engine=white,
        initial_sfen="startpos",
        game_id=game_id,
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lag_ms", "reclassify", "coverage_complete", "expected_result", "expected_origin"),
    [
        (10_000.0, True, True, GameResult.ERROR, "unknown"),
        (0.0, True, True, GameResult.WHITE_WIN_BY_TIMEOUT, "engine_deadline"),
        (5.0, True, True, GameResult.WHITE_WIN_BY_TIMEOUT, "engine_deadline"),
        (0.0, True, False, GameResult.ERROR, "unknown"),
        (10_000.0, False, True, GameResult.WHITE_WIN_BY_TIMEOUT, "unknown"),
    ],
)
async def test_run_game_timeout_reclassification_outcomes(
    lag_ms: float,
    reclassify: bool,
    coverage_complete: bool,
    expected_result: GameResult,
    expected_origin: str,
) -> None:
    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    record = await _run_timeout_game(
        limits,
        lag_ms=lag_ms,
        reclassify=reclassify,
        coverage_complete=coverage_complete,
        game_id="game_reclassify",
    )
    assert record.result == expected_result
    assert record.metadata.attributes.get("timeout_origin") == expected_origin
