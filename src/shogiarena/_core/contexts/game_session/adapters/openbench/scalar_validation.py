"""Shared coercion helpers for OpenBench adapters."""

from __future__ import annotations

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int


def _require_int(value: object, *, field_name: str) -> int:
    parsed = coerce_int(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be an integer")
    return parsed


def _require_float(value: object, *, field_name: str) -> float:
    parsed = coerce_float(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _require_positive_int(value: object, *, field_name: str) -> int:
    parsed = _require_int(value, field_name=field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return parsed
