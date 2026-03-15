"""Utility helpers for snapshot normalization and JSON coercion."""

from __future__ import annotations

from typing import Literal

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize


def sanitize_engine_io_line_optional(line: str | None) -> str | None:
    if not isinstance(line, str):
        return None
    cleaned = line.strip()
    if not cleaned:
        return None
    return cleaned


def _coerce_str_list(raw: JsonValue | None) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        value = coerce_str(item)
        if value is not None:
            values.append(value)
    return values


def coerce_int_list(raw: JsonValue | None) -> list[int]:
    if not isinstance(raw, list):
        return []
    values: list[int] = []
    for item in raw:
        value = coerce_int(item)
        if value is not None:
            values.append(value)
    return values


def coerce_bool_list(raw: JsonValue | None) -> list[bool]:
    if not isinstance(raw, list):
        return []
    values: list[bool] = []
    for item in raw:
        if isinstance(item, bool):
            values.append(item)
    return values


def normalize_role(role: str | None) -> Literal["black", "white"] | None:
    if not isinstance(role, str):
        return None
    normalized = role.strip().lower()
    if normalized == "black":
        return "black"
    if normalized == "white":
        return "white"
    return None


def to_json_value(value: object | None) -> JsonValue | None:
    if value is None:
        return None
    return json_serialize(value)


__all__ = [
    "coerce_bool_list",
    "coerce_int_list",
    "_coerce_str_list",
    "is_str_object_mapping",
    "normalize_role",
    "sanitize_engine_io_line_optional",
    "to_json_value",
]
