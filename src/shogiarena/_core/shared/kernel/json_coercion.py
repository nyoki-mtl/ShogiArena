"""Shared JSON conversion and coercion helpers.

This module is the canonical owner of all JSON object conversion logic.
Helpers are organized by trust level:

- **Type guards** (``is_str_object_mapping``): runtime type narrowing.
- **Trusted casts** (``to_json_object``, ``to_json_object_or_empty``):
  caller guarantees the input is a mapping; values are serialized via
  ``json_serialize``.
- **Validated coercion** (``coerce_json_*``): input is checked at runtime
  and ``TypeError`` is raised (or ``None`` returned) on invalid shapes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias, TypeGuard

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CoercibleJsonValue: TypeAlias = JsonValue | Path | Mapping[str, "CoercibleJsonValue"] | Sequence["CoercibleJsonValue"]

# ---------------------------------------------------------------------------
# Type guards
# ---------------------------------------------------------------------------


def is_str_object_mapping(value: object) -> TypeGuard[Mapping[str, JsonValue]]:
    """Return ``True`` when *value* is a mapping with string keys."""

    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value.keys())


# ---------------------------------------------------------------------------
# Trusted casts – caller guarantees input shape
# ---------------------------------------------------------------------------


def to_json_object(value: Mapping[str, object]) -> JsonObject:
    """Normalize mapping keys/values into JSON-safe object payload."""

    return {str(key): json_serialize(item) for key, item in value.items()}


def to_json_object_or_empty(value: object | Mapping[str, object] | None) -> JsonObject:
    """Return ``to_json_object(value)`` when mapping, otherwise ``{}``."""

    if not isinstance(value, Mapping):
        return {}
    return {str(key): json_serialize(item) for key, item in value.items()}


# ---------------------------------------------------------------------------
# Validated coercion – runtime checked, raises or returns None
# ---------------------------------------------------------------------------


def coerce_json_value(value: object | CoercibleJsonValue, *, field_name: str) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): coerce_json_value(item, field_name=field_name) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [coerce_json_value(item, field_name=field_name) for item in value]
    raise TypeError(f"{field_name} must contain only JSON-compatible values")


def coerce_json_object(value: object | Mapping[str, JsonValue] | None, *, field_name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return {str(key): coerce_json_value(item, field_name=field_name) for key, item in value.items()}


def coerce_json_object_serialized(value: object | Mapping[str, object] | None, *, field_name: str) -> JsonObject:
    serialized = json_serialize(value)
    if not isinstance(serialized, dict):
        raise TypeError(f"{field_name} must be a mapping")
    return serialized


def coerce_json_object_or_none(value: object | None) -> JsonObject | None:
    serialized = json_serialize(value)
    if not isinstance(serialized, dict):
        return None
    return serialized


__all__ = [
    "coerce_json_object",
    "coerce_json_object_or_none",
    "coerce_json_object_serialized",
    "coerce_json_value",
    "is_str_object_mapping",
    "to_json_object",
    "to_json_object_or_empty",
]
