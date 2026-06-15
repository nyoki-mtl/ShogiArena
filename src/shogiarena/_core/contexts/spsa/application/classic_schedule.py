"""Classic SPSA gain schedule helpers."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry


@dataclass(frozen=True, slots=True)
class ClassicSchedulePoint:
    """One update's pair-based classic SPSA schedule values."""

    pair_index_start: int
    pair_index_end: int
    k_pair: int
    k_total: int
    A_abs: float
    c: dict[str, float]
    r: dict[str, float]


def compute_classic_schedule_point(
    *,
    params: list[ParamEntry],
    num_updates: int,
    pairs_per_update: int,
    update_idx: int,
    alpha: float,
    gamma: float,
    a_mode: str,
    a_value: float,
    int_ck_floor: float,
) -> ClassicSchedulePoint:
    """Compute pair-based classic SPSA schedule values for one update."""
    if num_updates <= 0:
        raise ValueError("num_updates must be positive")
    if pairs_per_update <= 0:
        raise ValueError("pairs_per_update must be positive")
    if update_idx <= 0:
        raise ValueError("update_idx must be 1-based and positive")
    pair_index_start = 1 + (update_idx - 1) * pairs_per_update
    pair_index_end = update_idx * pairs_per_update
    k_total = num_updates * pairs_per_update
    k_pair = pair_index_end
    A_abs = float(a_value) * float(k_total) if a_mode == "ratio" else float(a_value)
    c_values: dict[str, float] = {}
    r_values: dict[str, float] = {}
    for param in params:
        if param.is_not_used:
            continue
        c_i = float(param.step) * (float(k_total) ** gamma) / (float(k_pair) ** gamma)
        if param.type == "int":
            c_i = max(c_i, float(int_ck_floor))
        if c_i <= 0.0:
            # SPSA requires c_k > 0; guard against a divide-by-zero in r_i for a misconfigured
            # (e.g. step == 0) float parameter rather than raising an opaque ZeroDivisionError.
            raise ValueError(f"SPSA c_k must be > 0 for parameter {param.name!r} (step={param.step})")
        r_i = (
            float(param.delta)
            * (float(param.step) ** 2)
            * ((A_abs + float(k_total)) ** alpha)
            / (((A_abs + float(k_pair)) ** alpha) * (c_i**2))
        )
        c_values[param.name] = float(c_i)
        r_values[param.name] = float(r_i)
    return ClassicSchedulePoint(
        pair_index_start=pair_index_start,
        pair_index_end=pair_index_end,
        k_pair=k_pair,
        k_total=k_total,
        A_abs=A_abs,
        c=c_values,
        r=r_values,
    )


__all__ = ["ClassicSchedulePoint", "compute_classic_schedule_point"]
