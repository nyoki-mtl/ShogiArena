"""Shared confidence interval primitives."""

from __future__ import annotations

from statistics import NormalDist

DEFAULT_CONFIDENCE = 0.95


def validate_confidence(confidence: float) -> None:
    """confidence が開区間 (0, 1) に入ることを検証する。"""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in the open interval (0, 1)")


def normal_two_sided_z(confidence: float = DEFAULT_CONFIDENCE) -> float:
    """両側信頼区間で使う標準正規分布の z 値を返す。"""
    validate_confidence(confidence)
    alpha = 1.0 - confidence
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


def normal_margin(std_error: float | None, *, confidence: float = DEFAULT_CONFIDENCE) -> float | None:
    """標準誤差から両側正規近似の margin を計算する。"""
    if std_error is None:
        return None
    return normal_two_sided_z(confidence) * std_error


def normal_probability_interval(
    mean: float | None,
    std_error: float | None,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
) -> tuple[float, float] | None:
    """確率値の正規近似 interval を [0, 1] に丸めて返す。"""
    if mean is None or std_error is None:
        return None
    margin = normal_margin(std_error, confidence=confidence)
    if margin is None:
        return None
    return max(0.0, mean - margin), min(1.0, mean + margin)


def wilson_score_interval(
    *,
    successes: float,
    trials: int,
    confidence: float = DEFAULT_CONFIDENCE,
) -> tuple[float, float] | None:
    """Wilson score interval を [0, 1] に丸めて返す。"""
    validate_confidence(confidence)
    if trials <= 0:
        return None
    z = normal_two_sided_z(confidence)
    n = float(trials)
    phat = successes / n
    denominator = 1.0 + (z * z / n)
    center = (phat + (z * z) / (2.0 * n)) / denominator
    margin = z * ((phat * (1.0 - phat) / n + (z * z) / (4.0 * n * n)) ** 0.5) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


__all__ = [
    "DEFAULT_CONFIDENCE",
    "normal_margin",
    "normal_probability_interval",
    "normal_two_sided_z",
    "validate_confidence",
    "wilson_score_interval",
]
