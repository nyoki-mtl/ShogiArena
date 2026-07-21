from __future__ import annotations

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSY = frozenset({"0", "false", "f", "no", "n", "off"})


def coerce_bool(value: object | None) -> bool:
    """Convert value to ``bool`` with a conservative string parser."""

    match value:
        case bool() as bvalue:
            return bvalue
        case int() | float():
            return bool(value)
        case str() as svalue:
            return svalue.strip().lower() in _TRUTHY
        case _:
            return False


def coerce_optional_bool(value: object | None) -> bool | None:
    """Convert value to ``bool`` while preserving unknown/null values."""

    match value:
        case None:
            return None
        case bool() as bvalue:
            return bvalue
        case int() | float():
            return bool(value)
        case str() as svalue:
            normalized = svalue.strip().lower()
            if normalized in _TRUTHY:
                return True
            if normalized in _FALSY:
                return False
            return None
        case _:
            return None


__all__ = ["coerce_bool", "coerce_optional_bool"]
