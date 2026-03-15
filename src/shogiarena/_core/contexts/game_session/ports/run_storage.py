"""Shared run storage protocol for runtime session components."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from shogiarena._core.shared.kernel.database_types import DatabaseServicePort
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


class RunStoragePort(Protocol):
    run_dir: Path

    def db_service(self) -> DatabaseServicePort: ...
    def read_json(self, relative_path: str) -> JsonObject | None: ...
    def write_json(self, relative_path: str, payload: JsonValue, *, indent: int = 2) -> Path: ...


__all__ = ["RunStoragePort"]
