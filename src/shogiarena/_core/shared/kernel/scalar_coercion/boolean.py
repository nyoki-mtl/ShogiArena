from __future__ import annotations

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})


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


__all__ = ["coerce_bool"]
