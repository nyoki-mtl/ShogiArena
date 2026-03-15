"""Parsing and normalization helpers for USI engine configuration mappings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    OptionalText,
    coerce_optional_text,
    coerce_str_list,
    strict_int,
)
from shogiarena._core.shared.kernel.serialization import json_serialize

BoolLike: TypeAlias = str | int | float | bool | None
FloatLike: TypeAlias = str | int | float | None
StringListLike: TypeAlias = str | list[str] | tuple[str, ...] | None
_ConfigValue: TypeAlias = JsonScalar | list[JsonScalar] | dict[str, JsonScalar]
_ConfigObject: TypeAlias = dict[str, _ConfigValue]


def _coerce_config_value(value: JsonValue, *, field: str) -> _ConfigValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        values: list[JsonScalar] = []
        for item in value:
            if item is None or isinstance(item, str | int | float | bool):
                values.append(item)
                continue
            raise TypeError(f"{field} list values must be scalar JSON values")
        return values
    if isinstance(value, Mapping):
        values: dict[str, JsonScalar] = {}
        for key, item in value.items():
            if item is None or isinstance(item, str | int | float | bool):
                values[str(key)] = item
                continue
            raise TypeError(f"{field}.{key} must be a scalar JSON value")
        return values
    raise TypeError(f"{field} must be a scalar, scalar-list, or scalar mapping")


class _UsiEngineMappingInput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: OptionalText = None
    engine_path: OptionalText = None
    artifact: OptionalText = None
    working_dir: OptionalText = None
    working_directory: OptionalText = None
    engine_args: StringListLike = None
    env: _ConfigObject = Field(default_factory=dict)
    options: _ConfigObject = Field(default_factory=dict)
    go_options: _ConfigObject = Field(default_factory=dict)
    build_options: _ConfigObject = Field(default_factory=dict)
    is_early_ponder_enabled: BoolLike = Field(default=False, alias="enable_early_ponder")
    handshake_timeout: FloatLike = None
    mate_default_ply_limit: FloatLike = None
    mate_default_node_limit: FloatLike = None
    is_mate_default_infinite: BoolLike = Field(default=False, alias="mate_default_infinite")
    should_mate_wait_for_bestmove: BoolLike = Field(default=False, alias="mate_wait_for_bestmove")
    isready_sync_strategy: str = "direct"
    isready_lock_key: str | None = None
    isready_lock_template: str | None = None
    isready_lock_check_key: str | None = None
    isready_lock_check_template: str | None = None
    isready_lock_check_templates: StringListLike = None
    should_skip_isready_lock_if_exists: BoolLike = Field(default=False, alias="isready_lock_skip_if_exists")

    @field_validator("isready_sync_strategy", mode="before")
    @classmethod
    def _coerce_sync_strategy(cls, value: JsonValue | None) -> str:
        return coerce_optional_text(value) or "direct"

    @field_validator(
        "isready_lock_key",
        "isready_lock_template",
        "isready_lock_check_key",
        "isready_lock_check_template",
        mode="before",
    )
    @classmethod
    def _coerce_optional_lock_str(cls, value: JsonValue | None) -> str | None:
        return coerce_optional_text(value)

    @field_validator("env", "options", "go_options", "build_options", mode="before")
    @classmethod
    def _coerce_mapping_fields(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> _ConfigObject:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("must be a mapping")
        normalized: _ConfigObject = {}
        for key, item in value.items():
            serialized = json_serialize(item)
            normalized[str(key)] = _coerce_config_value(serialized, field=f"mapping.{key}")
        return normalized

    @field_validator("engine_args", mode="before")
    @classmethod
    def _coerce_engine_args(cls, value: JsonValue | tuple[JsonValue, ...] | None) -> StringListLike:
        if value is None:
            return None
        if isinstance(value, str | bytes):
            raise TypeError("engine_args must be an iterable of arguments, not a string")
        if isinstance(value, list | tuple):
            normalized: list[str] = [str(arg) for arg in value]
            return tuple(normalized) if isinstance(value, tuple) else normalized
        raise TypeError("engine_args must be an iterable of arguments")

    @field_validator("isready_lock_check_templates", mode="before")
    @classmethod
    def _coerce_lock_templates(cls, value: JsonValue | tuple[JsonValue, ...] | None) -> StringListLike:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list | tuple):
            normalized: list[str] = [str(item) for item in value]
            return tuple(normalized) if isinstance(value, tuple) else normalized
        raise TypeError("isready_lock_check_templates must be a string or list")


def to_string_dict(mapping: Mapping[str, _ConfigValue], *, field: str) -> JsonObject:
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{field} must be a mapping; got {type(mapping).__name__}")
    return {str(key): json_serialize(value) for key, value in mapping.items()}


def to_env_dict(mapping: Mapping[str, _ConfigValue]) -> dict[str, str]:
    env = to_string_dict(mapping, field="env")
    return {key: str(value) for key, value in env.items()}


def to_bool(value: BoolLike, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        if value == 0 or value == 0.0:
            return False
        if value == 1 or value == 1.0:
            return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise TypeError(f"{field} must be a boolean-compatible value; got {type(value).__name__}")


def to_float(value: FloatLike, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return float(trimmed)
        except ValueError as exc:
            raise TypeError(f"{field} must be a float value; got {value!r}") from exc
    raise TypeError(f"{field} must be a float-compatible value; got {type(value).__name__}")


def normalize_engine_args(raw_args: StringListLike) -> tuple[str, ...]:
    if raw_args is None:
        return ()
    if isinstance(raw_args, str | bytes):
        raise TypeError("engine_args must be an iterable of arguments, not a string")
    try:
        return tuple(str(arg) for arg in raw_args)
    except TypeError as exc:  # e.g. not iterable
        raise TypeError("engine_args must be an iterable of arguments") from exc


def _normalize_optional_str(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string; got {type(value).__name__}")
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed


def _normalize_optional_str_list(value: StringListLike, *, field: str) -> tuple[str, ...]:
    return tuple(coerce_str_list(value, field=field))


def _normalize_optional_positive_int(value: FloatLike, *, field: str) -> int | None:
    if value is None:
        return None
    parsed = strict_int(value)
    if parsed is None and isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            parsed = int(trimmed)
        except ValueError as exc:
            raise TypeError(f"{field} must be a positive integer; got {value!r}") from exc
    if parsed is None:
        raise TypeError(f"{field} must be a positive integer; got {type(value).__name__}")
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0")
    return parsed


_TEMPLATE_PATTERN = re.compile(r"{([^{}]+)}")


def _render_template(template: str, mapping: Mapping[str, JsonValue]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = mapping.get(key)
        if value is None:
            return match.group(0)
        return str(value)

    return _TEMPLATE_PATTERN.sub(_replace, template)


def _has_unresolved_placeholder(value: str) -> bool:
    return _TEMPLATE_PATTERN.search(value) is not None
