"""Run-scoped storage implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.database_types import DatabaseServicePort
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


@dataclass(slots=True)
class RunStorage:
    """Run-scoped storage abstraction for runner artifacts."""

    run_dir: Path
    _db_factory: SQLiteShogiDBFactory
    _db_service: ArenaDBAdapter | None = None

    def db_service(self) -> DatabaseServicePort:
        if self._db_service is None:
            self._db_service = ArenaDBAdapter(self._db_factory)
        return self._db_service

    def resolve_path(self, relative_path: str) -> Path:
        return (self.run_dir / relative_path).resolve()

    def read_json(self, relative_path: str) -> JsonObject | None:
        path = self.resolve_path(relative_path)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{relative_path} must contain a JSON object")
        return data

    def write_json(self, relative_path: str, payload: JsonValue, *, indent: int = 2) -> Path:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=indent)
        return path


class FilesystemRunStorage(RunStorage):
    """Persistent storage rooted at a run directory."""

    def __init__(self, run_dir: Path) -> None:
        resolved = run_dir.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        super().__init__(
            run_dir=resolved,
            _db_factory=SQLiteShogiDBFactory(resolved / "game.db"),
        )


__all__ = ["FilesystemRunStorage", "RunStorage"]
