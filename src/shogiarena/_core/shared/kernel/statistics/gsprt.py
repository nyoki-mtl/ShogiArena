"""Generalized SPRT (GSPRT) log-likelihood-ratio computation.

Pure numerical helpers for the sequential probability ratio test, following the fishtest
``LLRcalc`` Brownian-motion approximation (see
https://www.cantate.be/Fishtest/GSPRT_approximation.pdf). The same formula serves both the
trinomial (per-game L/D/W) and pentanomial (per-pair LL/LD+DL/LW+DD+WL/DW+WD/WW) score
histograms; only the number of bins differs.

These functions are intentionally dependency-free (no scipy): the LLR needed for the pass/fail
decision only requires the empirical mean/variance and the logistic Elo->score transform.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Small prior mixed into empty bins so a degenerate histogram (e.g. all draws) keeps a strictly
# positive variance instead of dividing by zero. Matches fishtest LLRcalc.regularize.
_REGULARIZATION_EPSILON = 1e-3

# Valid histogram widths: 3 = trinomial (L, D, W), 5 = pentanomial pair outcomes.
_TRINOMIAL_BINS = 3
_PENTANOMIAL_BINS = 5


def sprt_bounds(alpha: float, beta: float) -> tuple[float, float]:
    """Return the (lower, upper) Wald LLR decision bounds for the given error rates."""
    if not math.isfinite(alpha) or not math.isfinite(beta):
        raise ValueError("alpha and beta must be finite")
    if not (0.0 < alpha < 1.0) or not (0.0 < beta < 1.0):
        raise ValueError("alpha and beta must be between 0 and 1")
    if alpha + beta >= 1.0:
        raise ValueError("alpha + beta must be < 1")
    return math.log(beta / (1.0 - alpha)), math.log((1.0 - beta) / alpha)


def elo_to_score(elo: float) -> float:
    """Logistic Elo difference -> expected score in (0, 1)."""
    if not math.isfinite(elo):
        raise ValueError("elo must be finite")
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def _validate_counts(counts: Sequence[float]) -> list[float]:
    if len(counts) not in (_TRINOMIAL_BINS, _PENTANOMIAL_BINS):
        raise ValueError(f"GSPRT histogram must have 3 or 5 bins, got {len(counts)}")
    validated = [float(count) for count in counts]
    if any(not math.isfinite(count) for count in validated):
        raise ValueError("GSPRT histogram counts must be finite")
    if any(count < 0.0 for count in validated):
        raise ValueError("GSPRT histogram counts must be non-negative")
    return validated


def _regularize(counts: Sequence[float]) -> list[float]:
    return [count if count != 0 else _REGULARIZATION_EPSILON for count in counts]


def _score_pdf(counts: Sequence[float]) -> tuple[float, list[tuple[float, float]]]:
    """Return (N, pdf) where pdf maps the per-game-equivalent score of each bin to its
    probability. For ``count`` bins the scores are ``i / (count - 1)`` for ``i`` in ``0..count-1``
    (trinomial -> {0, 0.5, 1}; pentanomial -> {0, 0.25, 0.5, 0.75, 1})."""
    regularized = _regularize(_validate_counts(counts))
    total = sum(regularized)
    last = len(regularized) - 1
    pdf = [(index / last, value / total) for index, value in enumerate(regularized)]
    return total, pdf


def score_mean_variance(counts: Sequence[float]) -> tuple[float, float]:
    """Return the (mean, variance) of the per-game-equivalent score for the histogram."""
    _total, pdf = _score_pdf(counts)
    mean = math.fsum(prob * value for value, prob in pdf)
    variance = math.fsum(prob * (value - mean) ** 2 for value, prob in pdf)
    return mean, variance


def compute_llr(counts: Sequence[float], *, elo0: float, elo1: float) -> float:
    """Generalized SPRT log-likelihood ratio for a trinomial or pentanomial score histogram.

    ``counts`` holds the bin frequencies (length 3 or 5). ``elo0``/``elo1`` are the null and
    alternative logistic Elo differences. Returns ``N * mu`` where ``mu`` is the per-sample LLR
    drift of the Brownian approximation.
    """
    validated = _validate_counts(counts)
    if elo1 <= elo0:
        raise ValueError(f"elo1 ({elo1}) must be greater than elo0 ({elo0})")
    if sum(validated) == 0.0:
        return 0.0
    total, pdf = _score_pdf(validated)
    mean = math.fsum(prob * value for value, prob in pdf)
    variance = math.fsum(prob * (value - mean) ** 2 for value, prob in pdf)
    s0 = elo_to_score(elo0)
    s1 = elo_to_score(elo1)
    drift = (mean - (s0 + s1) / 2.0) * (s1 - s0) / variance
    return total * drift


__all__ = [
    "compute_llr",
    "elo_to_score",
    "score_mean_variance",
    "sprt_bounds",
]
