"""JSON-safe recursive serialization utilities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from shogiarena.utils.types.types import JsonValue


def json_serialize(value: object) -> JsonValue:
    """Recursively convert complex types to JSON-serializable primitives.

    Handles BaseModel, dataclass, Enum, Path, Mapping, Sequence, set, tuple.
    Scalars (int, float, str, bool, None) are passed through unchanged.
    """
    match value:
        case None:
            return None
        case BaseModel() as model:
            return json_serialize(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return json_serialize(asdict(dc))
        case Enum() as e:
            return e.value
        case Path() as p:
            return str(p)
        case Mapping() as m:
            return {str(k): json_serialize(v) for k, v in m.items()}
        case set() as s:
            return [json_serialize(v) for v in sorted(s, key=repr)]
        case str() | int() | float() | bool() as scalar:
            return scalar
        case bytes() | bytearray():
            return str(value)
        case Sequence() as seq:
            return [json_serialize(v) for v in seq]
        case _:
            return str(value)
