"""Shared types and schema parsing for artifact resolution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object as _coerce_json_object_strict,
)
from shogiarena._core.shared.kernel.json_types import JsonScalar, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import OptionalText

_BuildTemplateValue = SimpleNamespace | str
_BuildTemplateContext = Mapping[str, _BuildTemplateValue]
_BuildConfigValue: TypeAlias = JsonScalar | list[JsonScalar] | dict[str, JsonScalar]
_BuildConfigObject: TypeAlias = dict[str, _BuildConfigValue]


def _coerce_build_config_scalar(value: object, *, field_name: str) -> JsonScalar:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"{field_name} must contain scalar values")


def _coerce_build_config_value(value: object, *, field_name: str) -> _BuildConfigValue:
    if isinstance(value, list):
        normalized_items: list[JsonScalar] = []
        for index, item in enumerate(value):
            normalized_items.append(_coerce_build_config_scalar(item, field_name=f"{field_name}[{index}]"))
        return normalized_items
    if isinstance(value, Mapping):
        normalized_map: dict[str, JsonScalar] = {}
        for key, item in value.items():
            normalized_map[str(key)] = _coerce_build_config_scalar(item, field_name=f"{field_name}.{key}")
        return normalized_map
    return _coerce_build_config_scalar(value, field_name=field_name)


def _coerce_build_config_object(
    value: JsonValue | Mapping[str, JsonValue] | None,
    *,
    field_name: str,
) -> _BuildConfigObject:
    mapping = _coerce_json_object_strict(value, field_name=field_name)
    normalized: _BuildConfigObject = {}
    for key, item in mapping.items():
        normalized[key] = _coerce_build_config_value(item, field_name=f"{field_name}.{key}")
    return normalized


class _ArtifactBuildConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    defaults: _BuildConfigObject = Field(default_factory=dict)
    git: _BuildConfigObject = Field(default_factory=dict)
    work_dir: OptionalText = None
    source_dir: OptionalText = None
    tune: bool | _BuildConfigObject | None = None
    hash: _BuildConfigObject | None = None

    @field_validator("defaults", "git", "hash", mode="before")
    @classmethod
    def _coerce_mapping_fields(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> _BuildConfigObject | None:
        if value is None:
            return None
        return _coerce_build_config_object(value, field_name="build_config mapping")

    @field_validator("tune", mode="before")
    @classmethod
    def _coerce_tune(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> bool | _BuildConfigObject | None:
        if value is None or isinstance(value, bool):
            return value
        return _coerce_build_config_object(value, field_name="build_config.tune")


@dataclass(frozen=True)
class ArtifactId:
    engine: str
    commit: str

    @classmethod
    def parse(cls, s: str) -> ArtifactId:
        raw = s.strip()
        match = re.match(r"^([A-Za-z0-9._-]+)/([A-Fa-f0-9]{6,40})$", raw)
        if not match:
            raise ValueError(f"Invalid artifact id: {s}. Expected '<repo>/<commit_hash>' (hex).")
        return cls(engine=match.group(1), commit=match.group(2))


__all__ = [
    "_ArtifactBuildConfigModel",
    "_BuildTemplateContext",
    "_BuildTemplateValue",
    "ArtifactId",
]
