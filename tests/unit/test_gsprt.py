"""Tests for the GSPRT pure-math helpers, validated against fishtest LLRcalc reference values."""

from __future__ import annotations

import math

import pytest

from shogiarena._core.shared.kernel.statistics.gsprt import (
    compute_llr,
    elo_to_score,
    score_mean_variance,
    sprt_bounds,
)

# Reference LLR values produced by fishtest's LLRcalc (Brownian GSPRT approximation) for
# elo0=0, elo1=5. The histograms are L/D/W (trinomial) or pair LL.../WW (pentanomial).
_REFERENCE_LLR = {
    (10, 70, 20): 0.460509634555,
    (30, 40, 30): -0.017256400267,
    (20, 60, 120): 3.151797025534,
    (5, 20, 50, 20, 5): -0.051769200800,
    (2, 10, 40, 30, 18): 1.565363204435,
    (0, 0, 0, 0, 30): 1714.754865734902,
}


@pytest.mark.parametrize(("counts", "expected"), list(_REFERENCE_LLR.items()))
def test_compute_llr_matches_fishtest_reference(counts: tuple[int, ...], expected: float) -> None:
    assert compute_llr(list(counts), elo0=0.0, elo1=5.0) == pytest.approx(expected, abs=1e-9)


def test_sprt_bounds_are_symmetric_for_equal_error_rates() -> None:
    lower, upper = sprt_bounds(0.05, 0.05)
    assert lower == pytest.approx(math.log(0.05 / 0.95))
    assert upper == pytest.approx(math.log(0.95 / 0.05))
    assert lower == pytest.approx(-upper)


def test_elo_to_score() -> None:
    assert elo_to_score(0.0) == pytest.approx(0.5)
    assert elo_to_score(400.0) == pytest.approx(10.0 / 11.0)
    assert 0.0 < elo_to_score(-1000.0) < 0.5 < elo_to_score(1000.0) < 1.0


def test_score_mean_variance_trinomial() -> None:
    # Equal L/D/W -> mean score 0.5.
    mean, variance = score_mean_variance([10, 10, 10])
    assert mean == pytest.approx(0.5)
    assert variance > 0.0


def test_all_draws_has_near_zero_variance_but_does_not_divide_by_zero() -> None:
    # Regularization keeps the variance strictly positive so the LLR is finite.
    llr = compute_llr([0, 100, 0], elo0=0.0, elo1=5.0)
    assert math.isfinite(llr)


def test_empty_histogram_returns_zero_llr() -> None:
    assert compute_llr([0, 0, 0], elo0=0.0, elo1=5.0) == 0.0
    assert compute_llr([0, 0, 0, 0, 0], elo0=0.0, elo1=5.0) == 0.0


def test_winning_results_push_llr_positive() -> None:
    # A strong plus score should drive the LLR towards H1 (positive).
    assert compute_llr([5, 20, 75], elo0=0.0, elo1=5.0) > 0.0
    # A losing score should drive it negative.
    assert compute_llr([75, 20, 5], elo0=0.0, elo1=5.0) < 0.0


def test_pentanomial_lower_variance_than_trinomial_for_equivalent_data() -> None:
    # 100 game pairs all scoring 1.0 (one win + one loss each) = a perfectly balanced result.
    # The pentanomial pair distribution (all in the central bin) has lower variance than the
    # trinomial L/D/W view of the same 200 games, illustrating the variance-reduction benefit.
    _penta_mean, penta_var = score_mean_variance([0, 0, 100, 0, 0])
    _tri_mean, tri_var = score_mean_variance([100, 0, 100])
    assert penta_var < tri_var


def test_invalid_bin_count_raises() -> None:
    with pytest.raises(ValueError, match="3 or 5 bins"):
        compute_llr([1, 2, 3, 4], elo0=0.0, elo1=5.0)


def test_negative_counts_raise() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        compute_llr([-1, 2, 3], elo0=0.0, elo1=5.0)


def test_non_finite_counts_raise() -> None:
    with pytest.raises(ValueError, match="finite"):
        compute_llr([1, math.nan, 3], elo0=0.0, elo1=5.0)


def test_invalid_elo_order_raises() -> None:
    with pytest.raises(ValueError, match="must be greater"):
        compute_llr([10, 10, 10], elo0=5.0, elo1=0.0)


def test_invalid_sprt_bounds_raise() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        sprt_bounds(0.0, 0.05)
    with pytest.raises(ValueError, match="< 1"):
        sprt_bounds(0.8, 0.3)


def test_non_finite_elo_raises() -> None:
    with pytest.raises(ValueError, match="finite"):
        elo_to_score(math.inf)
