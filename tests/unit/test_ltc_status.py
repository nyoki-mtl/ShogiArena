"""Regression tests for LTC pass/fail status resolution."""

from __future__ import annotations

from types import SimpleNamespace

from shogiarena._core.contexts.spsa.adapters.runtime.ltc_regression_events import determine_ltc_status


def _criteria(*, min_winrate: float | None = None, max_elo_drop: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(min_winrate=min_winrate, max_elo_drop=max_elo_drop)


def test_max_elo_drop_fails_when_regression_exceeds_threshold() -> None:
    # max_elo_drop=10 tolerates a drop of at most 10 Elo; a -25 Elo result must fail (regression
    # for the previously unused max_elo_drop criterion).
    status, reasons = determine_ltc_status(
        _criteria(max_elo_drop=10.0),
        winrate=0.45,
        elo=-25.0,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "failed"
    assert any("elo" in reason.lower() for reason in reasons)


def test_max_elo_drop_passes_within_threshold() -> None:
    status, reasons = determine_ltc_status(
        _criteria(max_elo_drop=10.0),
        winrate=0.52,
        elo=-5.0,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "passed"
    assert reasons == []


def test_negative_max_elo_drop_uses_absolute_drop_threshold() -> None:
    status, reasons = determine_ltc_status(
        _criteria(max_elo_drop=-10.0),
        winrate=0.48,
        elo=-5.0,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "passed"
    assert reasons == []

    status, reasons = determine_ltc_status(
        _criteria(max_elo_drop=-10.0),
        winrate=0.45,
        elo=-25.0,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "failed"
    assert any("10.0" in reason for reason in reasons)


def test_max_elo_drop_ignored_when_elo_unavailable() -> None:
    status, _ = determine_ltc_status(
        _criteria(max_elo_drop=10.0),
        winrate=0.5,
        elo=None,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "passed"


def test_max_elo_drop_fails_all_loss_sample_with_negative_infinite_elo() -> None:
    status, reasons = determine_ltc_status(
        _criteria(max_elo_drop=10.0),
        winrate=0.0,
        elo=None,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "failed"
    assert reasons == ["elo -inf below allowed drop of 10.0"]


def test_no_criteria_passes() -> None:
    status, reasons = determine_ltc_status(
        _criteria(),
        winrate=0.30,
        elo=-50.0,
        sprt_payload=None,
        sprt_decision=None,
    )
    assert status == "passed"
    assert reasons == []
