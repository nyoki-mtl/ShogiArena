"""Atomic JSON file writing helpers."""

from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.shared.kernel.json_types import JsonValue


def write_json_atomic(path: Path, payload: JsonValue, *, indent: int = 2) -> None:
    """Write JSON via a temporary file and atomic rename."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    tmp_path.replace(path)


__all__ = ["write_json_atomic"]
