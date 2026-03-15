from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from shogiarena._core.shared.kernel.json_types import JsonScalar, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

EngineOptionValue: TypeAlias = JsonScalar | list[JsonScalar] | dict[str, JsonScalar]
EngineOptionMap: TypeAlias = dict[str, EngineOptionValue]


def _coerce_engine_option_value(raw: JsonValue | tuple[JsonValue, ...], *, field_name: str) -> EngineOptionValue:
    if raw is None or isinstance(raw, str | int | float | bool):
        return raw
    if isinstance(raw, list | tuple):
        values: list[JsonScalar] = []
        for item in raw:
            if item is None or isinstance(item, str | int | float | bool):
                values.append(item)
                continue
            raise TypeError(f"{field_name} list values must be scalar JSON values")
        return values
    if isinstance(raw, Mapping):
        values: dict[str, JsonScalar] = {}
        for key, value in raw.items():
            if value is None or isinstance(value, str | int | float | bool):
                values[str(key)] = value
                continue
            raise TypeError(f"{field_name}.{key} must be a scalar JSON value")
        return values
    raise TypeError(f"{field_name} must be a scalar, scalar-list, or scalar mapping")


def coerce_engine_option_map(raw: JsonValue | Mapping[str, JsonValue] | None, *, field_name: str) -> EngineOptionMap:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: EngineOptionMap = {}
    for key, value in raw.items():
        key_name = str(key)
        normalized[key_name] = _coerce_engine_option_value(
            json_serialize(value),
            field_name=f"{field_name}.{key_name}",
        )
    return normalized
