"""Private helpers for normalizing scalar-map payloads in SPSA I/O models."""

from __future__ import annotations

from shogiarena._core.shared.kernel.json_types import JsonScalar, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_optional_text

ScalarMap = dict[str, JsonScalar]
ScalarOrMap = JsonScalar | ScalarMap
ScalarOrMapMap = dict[str, ScalarOrMap]


def coerce_scalar_map(value: JsonValue | None) -> ScalarMap | None:
    if not isinstance(value, dict):
        return None
    payload: ScalarMap = {}
    for key, item in value.items():
        if item is None or isinstance(item, str | int | float | bool):
            normalized_key = coerce_optional_text(key)
            if normalized_key is not None:
                payload[normalized_key] = item
    return payload


def coerce_scalar_or_map_map(value: JsonValue | None) -> ScalarOrMapMap | None:
    if not isinstance(value, dict):
        return None
    payload: ScalarOrMapMap = {}
    for key, item in value.items():
        normalized_key = coerce_optional_text(key)
        if normalized_key is None:
            continue
        if item is None or isinstance(item, str | int | float | bool):
            payload[normalized_key] = item
            continue
        nested = coerce_scalar_map(item)
        if nested is not None:
            payload[normalized_key] = nested
    return payload


__all__ = ["ScalarMap", "ScalarOrMap", "ScalarOrMapMap", "coerce_scalar_map", "coerce_scalar_or_map_map"]
