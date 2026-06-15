"""Regression tests for draw-aware match confidence intervals (WDL sample variance)."""

from __future__ import annotations

import math

from shogiarena._core.interfaces.dashboard.match.api import MatchAPI


def _counts(wins: int, draws: int, losses: int) -> dict[str, int]:
    return {"wins": wins, "draws": draws, "losses": losses, "games": wins + draws + losses}


def test_score_std_error_matches_bernoulli_without_draws() -> None:
    counts = _counts(wins=5, draws=0, losses=5)
    expected = math.sqrt(0.5 * 0.5 / 10)  # Bernoulli p(1-p)/n when there are no draws
    assert MatchAPI._score_std_error(counts) == expected


def test_score_std_error_below_bernoulli_with_draws() -> None:
    counts = _counts(wins=3, draws=4, losses=3)
    bernoulli = math.sqrt(0.5 * 0.5 / 10)
    # Draws reduce the per-game score spread, so the draw-aware SE is smaller.
    assert MatchAPI._score_std_error(counts) < bernoulli


def test_score_std_error_zero_for_all_draws() -> None:
    # Every game scored 0.5 -> zero sample variance -> zero SE (not the Bernoulli 0.5 spread).
    assert MatchAPI._score_std_error(_counts(0, 10, 0)) == 0.0


def test_win_rate_ci_uses_draw_aware_interval() -> None:
    counts = _counts(wins=3, draws=4, losses=3)
    ci = MatchAPI._win_rate_confidence_interval(0.5, counts)
    std = MatchAPI._score_std_error(counts)
    assert ci["lower"] == max(0.0, 0.5 - 1.96 * std)
    assert ci["upper"] == min(1.0, 0.5 + 1.96 * std)


def test_ci_none_when_no_games() -> None:
    counts = _counts(0, 0, 0)
    assert MatchAPI._win_rate_confidence_interval(None, counts) == {"lower": None, "upper": None}
    assert MatchAPI._elo_confidence_interval(None, counts) == {"lower": None, "upper": None}
