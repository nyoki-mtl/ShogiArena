"""Timeout origin decision table（task 0052 / review finding H1・M4）。

lag の全量控除ではなく、clock deadline、``bestmove`` の観測上界、watchdog coverage から
判定することを固定する。

とくに次を回帰として押さえる。

- genuine engine delay と loop stall が同時に起きても ``orchestrator_stall`` にしない。
- 超過分が重なった lag より大きければ、lag があっても ``engine_deadline`` と判定する。
- positive evidence（deadline 前に観測済み）がある場合だけ ``orchestrator_stall`` にする。
- coverage 欠落や delivery coverage 不足を ``engine_deadline`` へ倒さない。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagObservation
from shogiarena._core.shared.kernel.timeout_attribution import (
    DeliveryCoverage,
    ObservationBasis,
    TimeoutEvidence,
    TimeoutOrigin,
    TimeoutWindow,
    classify_timeout_evidence,
    is_invalid_timeout_origin,
    is_invalid_timeout_origin_value,
)

_START_S = 1000.0


def _window(*, budget_ms: float | None = 200.0, clock_mode: str = "fixed") -> TimeoutWindow:
    return TimeoutWindow(
        started_at_s=_START_S,
        budget_ms=budget_ms,
        deadline_s=None if budget_ms is None else _START_S + budget_ms / 1000.0,
        clock_mode=clock_mode,
    )


def _coverage(
    *,
    lag_ms: float = 0.0,
    complete: bool = True,
    available: bool = True,
    in_flight_ms: float = 0.0,
) -> LoopLagObservation:
    return LoopLagObservation(
        lag_ms_in_window=lag_ms,
        in_flight_lag_ms=in_flight_ms,
        is_available=available,
        coverage_complete=complete,
        watchdog_generation=1,
        dropped_event_count=0,
        dropped_through_s=None,
    )


_LOCAL = DeliveryCoverage(basis=ObservationBasis.LOCAL_PIPE, is_covered=True)
_REMOTE = DeliveryCoverage(basis=ObservationBasis.REMOTE_TRANSPORT, is_covered=False)


def _evidence(
    *,
    window: TimeoutWindow | None = None,
    observed_at_s: float | None = None,
    delivery: DeliveryCoverage | None = _LOCAL,
    coverage: LoopLagObservation | None = None,
    detected_at_s: float = _START_S + 1.6,
    is_attribution_enabled: bool = True,
    is_transport_failure: bool = False,
) -> TimeoutEvidence:
    return TimeoutEvidence(
        site="loop_top_expired",
        detected_at_s=detected_at_s,
        is_attribution_enabled=is_attribution_enabled,
        window=_window() if window is None else window,
        bestmove_observed_at_s=observed_at_s,
        delivery=delivery,
        coverage=_coverage() if coverage is None else coverage,
        is_transport_failure=is_transport_failure,
    )


def _origin(
    *,
    window: TimeoutWindow | None = None,
    observed_at_s: float | None = None,
    delivery: DeliveryCoverage | None = _LOCAL,
    coverage: LoopLagObservation | None = None,
    detected_at_s: float = _START_S + 1.6,
    is_attribution_enabled: bool = True,
    is_transport_failure: bool = False,
) -> TimeoutOrigin:
    return classify_timeout_evidence(
        _evidence(
            window=window,
            observed_at_s=observed_at_s,
            delivery=delivery,
            coverage=coverage,
            detected_at_s=detected_at_s,
            is_attribution_enabled=is_attribution_enabled,
            is_transport_failure=is_transport_failure,
        )
    ).origin


# --- opt-in / opt-out --------------------------------------------------------


def test_attribution_disabled_keeps_the_legacy_loss_on_time() -> None:
    assert _origin(is_attribution_enabled=False) is TimeoutOrigin.UNATTRIBUTED


# --- wall-clock budget を持たない mode ---------------------------------------


def test_search_limits_wait_failure_is_a_transport_timeout_not_a_clock_timeout() -> None:
    origin = _origin(window=_window(budget_ms=None, clock_mode="search_limits"))
    assert origin is TimeoutOrigin.TRANSPORT_TIMEOUT


def test_protocol_wait_failure_is_a_transport_timeout() -> None:
    assert _origin(is_transport_failure=True) is TimeoutOrigin.TRANSPORT_TIMEOUT


# --- H1: engine delay と loop stall の同時発生 ------------------------------


def test_genuine_engine_delay_with_a_smaller_overlapping_stall_is_an_engine_deadline() -> None:
    """engine が 1400ms 使い、予算 200ms を 1200ms 超過。重なった lag はそれより小さい。

    旧実装（lag 全量控除）はこれを ``orchestrator_stall`` に倒しえた。
    """

    origin = _origin(
        observed_at_s=_START_S + 1.4,
        coverage=_coverage(lag_ms=900.0),
    )
    assert origin is TimeoutOrigin.ENGINE_DEADLINE


def test_stall_after_the_bestmove_does_not_change_the_verdict() -> None:
    """``bestmove`` を観測した後の停滞は、その観測が遅れた理由を説明できない。

    lag の窓は ``[窓の開始, 観測上界]`` なので、観測後の停滞は入力に入らない。
    """

    decision = classify_timeout_evidence(
        _evidence(
            observed_at_s=_START_S + 1.4,
            coverage=_coverage(lag_ms=0.0),
            detected_at_s=_START_S + 3.0,
        )
    )
    assert decision.origin is TimeoutOrigin.ENGINE_DEADLINE
    assert decision.overshoot_ms == pytest.approx(1200.0)


def test_simultaneous_engine_delay_and_explanatory_stall_is_unknown_not_orchestrator_stall() -> None:
    """因果を区別できない場合は ``unknown``。誤って ``orchestrator_stall`` と断定しない。"""

    origin = _origin(
        observed_at_s=_START_S + 1.4,
        coverage=_coverage(lag_ms=1500.0),
    )
    assert origin is TimeoutOrigin.UNKNOWN


@pytest.mark.parametrize(
    ("lag_ms", "expected"),
    [
        (1199.0, TimeoutOrigin.ENGINE_DEADLINE),  # overshoot > lag
        (1200.0, TimeoutOrigin.UNKNOWN),  # overshoot == lag（停滞だけで説明できてしまう）
        (1201.0, TimeoutOrigin.UNKNOWN),  # overshoot < lag
    ],
)
def test_explanatory_lag_boundary(lag_ms: float, expected: TimeoutOrigin) -> None:
    origin = _origin(observed_at_s=_START_S + 1.4, coverage=_coverage(lag_ms=lag_ms))
    assert origin is expected


def test_a_tiny_overlapping_in_flight_lag_does_not_make_a_timeout_unknown() -> None:
    """健全な loop でも in-flight lag は小さな正値を返す。それだけで ``unknown`` にしない。"""

    origin = _origin(
        observed_at_s=_START_S + 1.4,
        coverage=_coverage(lag_ms=3.0, in_flight_ms=3.0),
    )
    assert origin is TimeoutOrigin.ENGINE_DEADLINE


# --- positive orchestrator evidence -----------------------------------------


def test_bestmove_observed_before_the_deadline_is_an_orchestrator_stall() -> None:
    origin = _origin(observed_at_s=_START_S + 0.1, coverage=_coverage(lag_ms=0.0))
    assert origin is TimeoutOrigin.ORCHESTRATOR_STALL


def test_bestmove_observed_exactly_at_the_deadline_is_an_orchestrator_stall() -> None:
    """境界は GameClock の strict predicate（``duration > budget``）と一致させる。"""

    origin = _origin(observed_at_s=_START_S + 0.2, coverage=_coverage(lag_ms=0.0))
    assert origin is TimeoutOrigin.ORCHESTRATOR_STALL


def test_bestmove_observed_just_after_the_deadline_is_not_an_orchestrator_stall() -> None:
    origin = _origin(observed_at_s=_START_S + 0.201, coverage=_coverage(lag_ms=0.0))
    assert origin is TimeoutOrigin.ENGINE_DEADLINE


def test_positive_orchestrator_evidence_survives_missing_delivery_coverage() -> None:
    """deadline 前に観測できていること自体が証拠なので、delivery coverage を要求しない。"""

    origin = _origin(observed_at_s=_START_S + 0.1, delivery=_REMOTE)
    assert origin is TimeoutOrigin.ORCHESTRATOR_STALL


# --- 未観測（wait timeout） --------------------------------------------------


def test_unobserved_bestmove_with_complete_coverage_is_an_engine_deadline() -> None:
    origin = _origin(observed_at_s=None, detected_at_s=_START_S + 1.6, coverage=_coverage(lag_ms=100.0))
    assert origin is TimeoutOrigin.ENGINE_DEADLINE


def test_unobserved_bestmove_with_explanatory_lag_is_unknown() -> None:
    origin = _origin(observed_at_s=None, detected_at_s=_START_S + 1.6, coverage=_coverage(lag_ms=1500.0))
    assert origin is TimeoutOrigin.UNKNOWN


# --- coverage 欠落は engine_deadline へ倒さない ------------------------------


def test_missing_watchdog_coverage_is_unknown() -> None:
    evidence = _evidence(observed_at_s=_START_S + 1.4)
    without_coverage = replace(evidence, coverage=None)
    assert classify_timeout_evidence(without_coverage).origin is TimeoutOrigin.UNKNOWN


def test_unavailable_watchdog_is_unknown_not_zero_lag() -> None:
    origin = _origin(observed_at_s=_START_S + 1.4, coverage=_coverage(available=False))
    assert origin is TimeoutOrigin.UNKNOWN


def test_incomplete_coverage_from_ring_overflow_or_restart_is_unknown() -> None:
    origin = _origin(observed_at_s=_START_S + 1.4, coverage=_coverage(complete=False))
    assert origin is TimeoutOrigin.UNKNOWN


def test_remote_transport_without_delivery_coverage_is_unknown() -> None:
    origin = _origin(observed_at_s=_START_S + 1.4, delivery=_REMOTE)
    assert origin is TimeoutOrigin.UNKNOWN


def test_third_party_port_without_observation_capability_is_unknown() -> None:
    assert _origin(observed_at_s=_START_S + 1.4, delivery=None) is TimeoutOrigin.UNKNOWN


# --- 欠落・不正値は fail closed ----------------------------------------------


def test_missing_clock_window_is_unknown() -> None:
    without_window = replace(_evidence(), window=None)
    assert classify_timeout_evidence(without_window).origin is TimeoutOrigin.UNKNOWN


@pytest.mark.parametrize("bad_budget", [float("nan"), float("inf")])
def test_non_finite_budget_is_unknown(bad_budget: float) -> None:
    window = TimeoutWindow(
        started_at_s=_START_S,
        budget_ms=bad_budget,
        deadline_s=_START_S + 0.2,
        clock_mode="fixed",
    )
    assert _origin(window=window, observed_at_s=_START_S + 1.4) is TimeoutOrigin.UNKNOWN


def test_negative_budget_is_unknown() -> None:
    window = TimeoutWindow(started_at_s=_START_S, budget_ms=-1.0, deadline_s=_START_S - 0.1, clock_mode="fixed")
    assert _origin(window=window, observed_at_s=_START_S + 1.4) is TimeoutOrigin.UNKNOWN


def test_inverted_window_is_unknown() -> None:
    window = TimeoutWindow(started_at_s=_START_S, budget_ms=200.0, deadline_s=_START_S - 5.0, clock_mode="fixed")
    assert _origin(window=window, observed_at_s=_START_S + 1.4) is TimeoutOrigin.UNKNOWN


def test_non_finite_observation_time_is_unknown() -> None:
    assert _origin(observed_at_s=float("nan")) is TimeoutOrigin.UNKNOWN


# --- clock mode ごとの差 ------------------------------------------------------


@pytest.mark.parametrize("clock_mode", ["fixed", "time_increment", "time_byoyomi"])
def test_wall_clock_modes_share_the_same_decision_table(clock_mode: str) -> None:
    origin = _origin(
        window=_window(budget_ms=200.0, clock_mode=clock_mode),
        observed_at_s=_START_S + 1.4,
        coverage=_coverage(lag_ms=100.0),
    )
    assert origin is TimeoutOrigin.ENGINE_DEADLINE


# --- decision の付随情報 -----------------------------------------------------


def test_decision_carries_the_evidence_used_for_the_verdict() -> None:
    decision = classify_timeout_evidence(_evidence(observed_at_s=_START_S + 1.4, coverage=_coverage(lag_ms=42.0)))
    assert decision.site == "loop_top_expired"
    assert decision.reason == "overshoot-exceeds-lag"
    assert decision.clock_mode == "fixed"
    assert decision.budget_ms == pytest.approx(200.0)
    assert decision.lag_ms_in_window == pytest.approx(42.0)
    assert decision.is_coverage_complete is True
    assert decision.is_invalid is False


# --- invalid origin の集合 ---------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (TimeoutOrigin.ENGINE_DEADLINE, False),
        (TimeoutOrigin.UNATTRIBUTED, False),
        (TimeoutOrigin.ORCHESTRATOR_STALL, True),
        (TimeoutOrigin.UNKNOWN, True),
        (TimeoutOrigin.TRANSPORT_TIMEOUT, True),
    ],
)
def test_invalid_timeout_origins(origin: TimeoutOrigin, expected: bool) -> None:
    assert is_invalid_timeout_origin(origin) is expected
    assert is_invalid_timeout_origin_value(origin.value) is expected


def test_unknown_origin_strings_are_not_treated_as_invalid() -> None:
    assert is_invalid_timeout_origin_value(None) is False
    assert is_invalid_timeout_origin_value("something-else") is False
