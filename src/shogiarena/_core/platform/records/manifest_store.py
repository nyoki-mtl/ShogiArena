"""Manifest read/write helpers for binary record outputs."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject


def write_manifest(path: Path, payload: JsonObject) -> None:
    """Write the records manifest atomically."""

    write_json_atomic(path, payload)


__all__ = ["write_manifest"]
