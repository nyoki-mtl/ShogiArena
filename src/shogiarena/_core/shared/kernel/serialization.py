"""JSON-safe recursive serialization helpers for governed layers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from shogiarena._core.shared.kernel.json_types import JsonValue


def json_serialize(value: object) -> JsonValue:
    """Recursively convert complex values into JSON-serializable primitives."""

    match value:
        case None:
            return None
        case BaseModel() as model:
            return json_serialize(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return json_serialize(asdict(dc))
        case Enum() as enum_value:
            return enum_value.value
        case Path() as path_value:
            return str(path_value)
        case Mapping() as mapping_value:
            return {str(key): json_serialize(item) for key, item in mapping_value.items()}
        case set() as set_value:
            return [json_serialize(item) for item in sorted(set_value, key=repr)]
        case str() | int() | float() | bool() as scalar:
            return scalar
        case bytes() | bytearray():
            return str(value)
        case Sequence() as sequence_value:
            return [json_serialize(item) for item in sequence_value]
        case _:
            return str(value)


__all__ = ["json_serialize"]
