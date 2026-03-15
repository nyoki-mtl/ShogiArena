from __future__ import annotations

from shogiarena._core.shared.kernel.json_coercion import to_json_object_or_empty
from shogiarena._core.shared.kernel.scalar_coercion.numeric import coerce_float


def coerce_float_dict(raw: object | None) -> dict[str, float]:
    """Normalize mapping-like payload to ``dict[str, float]``."""

    raw_map = to_json_object_or_empty(raw)
    result: dict[str, float] = {}
    for key, value in raw_map.items():
        coerced = coerce_float(value)
        if coerced is not None:
            result[key] = coerced
    return result


def coerce_nested_float_dict(raw: object | None) -> dict[str, dict[str, float]]:
    """Normalize nested mapping payload to ``dict[str, dict[str, float]]``."""

    raw_map = to_json_object_or_empty(raw)
    result: dict[str, dict[str, float]] = {}
    for key, value in raw_map.items():
        nested = coerce_float_dict(value)
        if nested:
            result[key] = nested
    return result


__all__ = ["coerce_float_dict", "coerce_nested_float_dict"]
