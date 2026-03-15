from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

HashInput: TypeAlias = (
    JsonValue
    | Path
    | BaseModel
    | Mapping[str, "HashInput"]
    | list["HashInput"]
    | tuple["HashInput", ...]
    | set["HashInput"]
)


def normalize_for_hash(value: HashInput) -> JsonValue:
    match value:
        case BaseModel() as model:
            return normalize_for_hash(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return normalize_for_hash(asdict(dc))
        case Path() as path_obj:
            return str(path_obj)
        case dict() as mapping:
            return {
                str(key): normalize_for_hash(item)
                for key, item in sorted(mapping.items(), key=lambda pair: str(pair[0]))
            }
        case list() | tuple() as sequence:
            return [normalize_for_hash(item) for item in sequence]
        case set() as items:
            normalized_items = [normalize_for_hash(item) for item in items]
            return sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        case _:
            return json_serialize(value)


__all__ = ["HashInput", "normalize_for_hash"]
