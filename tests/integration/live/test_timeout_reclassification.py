"""Timeout attribution: an orchestrator stall must not be scored as an engine loss (task 0047).

These are the injection tests for the success criteria: with reclassification enabled, a timeout whose
move window is dominated by loop stall becomes an invalid game (ERROR), while a genuine engine overrun
(loop healthy) stays a loss on time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from rsshogi.core import Move

from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort, InfoHandler
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkResult
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagObservation
from shogiarena._core.shared.kernel.time_control import TimeControlLimits
from shogiarena._core.shared.kernel.timeout_attribution import ObservationBasis


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
        """ローカル pipe 相当の delivery coverage を宣言する（実 bridge と同じ扱い）。"""
        return ObservationBasis.LOCAL_PIPE.value


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


def _runner(
    limits: TimeControlLimits,
    *,
    lag_ms: float,
    reclassify: bool,
    coverage_complete: bool = True,
) -> GameRunner:
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=None,
        repetition_occurrences_to_draw=4,
    )
    runner.set_runtime_watchdog(_FakeWatchdog(lag_ms, coverage_complete=coverage_complete))
    runner.set_timeout_reclassification(reclassify)
    return runner


async def _run(runner: GameRunner, limits: TimeControlLimits, game_id: str):  # type: ignore[no-untyped-def]
    black = _SlowTimeoutEngine("black", delay_s=0.05)
    white = _SlowTimeoutEngine("white", delay_s=0.05)
    return await runner.run_game(
        black_engine=black,
        white_engine=white,
        initial_sfen="startpos",
        game_id=game_id,
        black_time_control_limits=limits,
        white_time_control_limits=limits,
    )


@pytest.mark.asyncio
async def test_explanatory_stall_is_invalidated_as_unknown_not_scored() -> None:
    # budget = fixed_time_ms + margin = 1ms; the ~50ms overshoot is fully explained by the
    # injected 10s loop lag, so the cause cannot be determined (task 0052 / Decision 2).
    # bestmove is never observed, so there is no positive orchestrator evidence either.
    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    runner = _runner(limits, lag_ms=10_000.0, reclassify=True)

    record = await _run(runner, limits, "game_loop_stall")

    # Not a decisive timeout, and excluded from the rating/SPRT sample (ERROR is non-decisive).
    assert record.result == GameResult.ERROR
    assert record.result != GameResult.WHITE_WIN_BY_TIMEOUT
    assert record.metadata.attributes.get("timeout_origin") == "unknown"


@pytest.mark.asyncio
async def test_engine_only_delay_is_still_a_loss_on_time() -> None:
    # Loop healthy (no lag): the engine genuinely blew its 1ms budget -> loss on time preserved.
    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    runner = _runner(limits, lag_ms=0.0, reclassify=True)

    record = await _run(runner, limits, "game_engine_delay")

    assert record.result == GameResult.WHITE_WIN_BY_TIMEOUT
    assert record.metadata.attributes.get("timeout_origin") == "engine_deadline"


@pytest.mark.asyncio
async def test_engine_delay_larger_than_the_overlapping_stall_stays_a_loss_on_time() -> None:
    """review finding H1: 超過分より小さい停滞が重なっただけでは無効局にしない。

    engine は約 50ms 使い、予算 1ms を約 49ms 超過する。重なった停滞は 5ms なので、
    観測遅延を最大に見積もっても実到着は deadline より後になる。
    """

    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    runner = _runner(limits, lag_ms=5.0, reclassify=True)

    record = await _run(runner, limits, "game_engine_delay_with_stall")

    assert record.result == GameResult.WHITE_WIN_BY_TIMEOUT
    assert record.metadata.attributes.get("timeout_origin") == "engine_deadline"


@pytest.mark.asyncio
async def test_coverage_loss_is_invalidated_rather_than_scored() -> None:
    """ring overflow や restart で窓を観測できていない場合は ``unknown``。"""

    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    runner = _runner(limits, lag_ms=0.0, reclassify=True, coverage_complete=False)

    record = await _run(runner, limits, "game_coverage_loss")

    assert record.result == GameResult.ERROR
    assert record.metadata.attributes.get("timeout_origin") == "unknown"


@pytest.mark.asyncio
async def test_reclassification_off_keeps_the_timeout_loss() -> None:
    # Same stall, but reclassification disabled: legacy loss-on-time is preserved (origin recorded).
    limits = TimeControlLimits(fixed_time_ms=1, expiry_margin_ms=0)
    runner = _runner(limits, lag_ms=10_000.0, reclassify=False)

    record = await _run(runner, limits, "game_stall_off")

    assert record.result == GameResult.WHITE_WIN_BY_TIMEOUT
