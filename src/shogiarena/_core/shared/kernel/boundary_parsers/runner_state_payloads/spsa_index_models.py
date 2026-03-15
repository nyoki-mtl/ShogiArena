from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IndexScalar = str | int | float | bool | None
_IndexScalarMap = dict[str, _IndexScalar]
_IndexMapValue = _IndexScalar | _IndexScalarMap
_IndexValue = _IndexScalar | list[_IndexScalar] | _IndexScalarMap | dict[str, _IndexMapValue] | list[_IndexScalarMap]
_IndexObject = dict[str, _IndexValue]


def _coerce_index_scalar(value: Any | None) -> _IndexScalar:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _coerce_index_value(value: Any | None) -> _IndexValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, _IndexMapValue] = {}
        for key, item in value.items():
            normalized[str(key)] = (
                {str(inner_key): _coerce_index_scalar(inner_value) for inner_key, inner_value in item.items()}
                if isinstance(item, Mapping)
                else _coerce_index_scalar(item)
            )
        return normalized
    if isinstance(value, list | tuple):
        if all(isinstance(item, Mapping) for item in value):
            entries: list[_IndexScalarMap] = []
            for item in value:
                item_map = {str(key): _coerce_index_scalar(item_value) for key, item_value in item.items()}
                entries.append(item_map)
            return entries
        return [_coerce_index_scalar(item) for item in value]
    return str(value)


def _coerce_index_object(value: Any | Mapping[str, object] | None) -> _IndexObject:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _coerce_index_value(item) for key, item in value.items()}


class _SpsaIndexPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    updates: list[_IndexObject] = Field(default_factory=list)
    metadata: _IndexObject = Field(default_factory=dict)

    @field_validator("updates", mode="before")
    @classmethod
    def _coerce_updates(cls, value: Any | None) -> list[_IndexObject]:
        if not isinstance(value, list):
            return []
        return [_coerce_index_object(entry) for entry in value]

    @field_validator("metadata", mode="before")
    @classmethod
    def _coerce_metadata(cls, value: Any | None) -> _IndexObject:
        return _coerce_index_object(value)


__all__ = ["_SpsaIndexPayload"]
