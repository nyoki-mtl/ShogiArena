from __future__ import annotations

import math


def is_strict_numeric(value: object | None) -> bool:
    """Return ``True`` only for ``int | float`` values excluding ``bool``."""

    match value:
        case bool():
            return False
        case int() | float():
            return True
        case _:
            return False


def strict_int(value: object | None) -> int | None:
    """Extract an ``int`` while excluding ``bool``."""

    match value:
        case bool():
            return None
        case int() as number:
            return number
        case _:
            return None


def coerce_int(value: object | None) -> int | None:
    """Convert value to ``int`` (reject ``bool`` and non-finite floats)."""

    match value:
        case bool():
            return None
        case int() as number:
            return number
        case float() as fvalue:
            return int(fvalue) if math.isfinite(fvalue) else None
        case str() as svalue:
            stripped = svalue.strip()
            if not stripped:
                return None
            try:
                return int(stripped)
            except ValueError:
                return None
        case _:
            return None


def coerce_non_negative_int(value: object | None) -> int | None:
    """Convert value to a non-negative ``int``."""

    parsed = coerce_int(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def coerce_int_strict(value: object | None, field: str = "value") -> int:
    """Convert value to ``int`` and raise ``ValueError`` on failure."""

    result = coerce_int(value)
    if result is None:
        raise ValueError(f"{field}: int に変換できません ({value!r})")
    return result


def coerce_non_negative_int_strict(
    value: object | None,
    *,
    message: str = "Expected a non-negative integer",
) -> int:
    """Convert value to non-negative ``int`` and raise ``ValueError`` on failure."""

    parsed = coerce_int(value)
    if parsed is None or parsed < 0:
        raise ValueError(message)
    return parsed


def coerce_optional_int_strict(value: object | None) -> int | None:
    """Convert value to ``int``; return ``None`` for ``None``, raise on failure."""

    if value is None:
        return None
    result = coerce_int(value)
    if result is None:
        raise ValueError(f"Expected an integer or null, got {value!r}")
    return result


def coerce_optional_non_negative_int_strict(value: object | None) -> int | None:
    """Convert value to non-negative ``int``; return ``None`` for ``None``, raise on failure."""

    if value is None:
        return None
    result = coerce_int(value)
    if result is None or result < 0:
        raise ValueError(f"Expected a non-negative integer or null, got {value!r}")
    return result


def coerce_float(value: object | None) -> float | None:
    """Convert value to finite ``float`` (reject ``bool``)."""

    match value:
        case bool():
            return None
        case int() | float() as number:
            fvalue = float(number)
            return fvalue if math.isfinite(fvalue) else None
        case str() as svalue:
            stripped = svalue.strip()
            if not stripped:
                return None
            try:
                fvalue = float(stripped)
            except ValueError:
                return None
            return fvalue if math.isfinite(fvalue) else None
        case _:
            return None


__all__ = [
    "coerce_float",
    "coerce_int",
    "coerce_int_strict",
    "coerce_non_negative_int",
    "coerce_non_negative_int_strict",
    "coerce_optional_int_strict",
    "coerce_optional_non_negative_int_strict",
    "is_strict_numeric",
    "strict_int",
]
