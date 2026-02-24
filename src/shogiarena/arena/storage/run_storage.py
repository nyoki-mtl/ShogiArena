from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.db.factory import BaseFactory, SQLiteShogiDBFactory
from shogiarena.utils.types.types import JsonObject


@dataclass(slots=True)
class RunStorage:
    """Run-scoped storage abstraction for runner artifacts."""

    run_dir: Path
    persistent: bool
    dashboard_compatible: bool
    _db_factory: BaseFactory

    def db_service(self) -> ArenaDBService:
        return ArenaDBService(self._db_factory)

    def resolve_path(self, relative_path: str) -> Path:
        return (self.run_dir / relative_path).resolve()

    def ensure_dir(self, relative_path: str) -> Path:
        path = self.resolve_path(relative_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self, relative_path: str) -> bool:
        return self.resolve_path(relative_path).exists()

    def read_json(self, relative_path: str) -> JsonObject | None:
        path = self.resolve_path(relative_path)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{relative_path} must contain a JSON object")
        return data

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def append_text(self, relative_path: str, content: str) -> Path:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def write_json(self, relative_path: str, payload: object, *, indent: int = 2) -> Path:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=indent)
        return path

    def delete(self, relative_path: str, *, missing_ok: bool = True) -> None:
        path = self.resolve_path(relative_path)
        if not path.exists():
            if missing_ok:
                return
            raise FileNotFoundError(f"Path not found: {path}")
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    def list_dir(self, relative_path: str, *, recursive: bool = False) -> list[Path]:
        path = self.resolve_path(relative_path)
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {path}")
        if recursive:
            return list(path.rglob("*"))
        return list(path.iterdir())

    def cleanup(self) -> None:
        return


class FilesystemRunStorage(RunStorage):
    """Persistent storage rooted at a run directory."""

    def __init__(self, run_dir: Path) -> None:
        resolved = run_dir.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        super().__init__(
            run_dir=resolved,
            persistent=True,
            dashboard_compatible=True,
            _db_factory=SQLiteShogiDBFactory(resolved / "game.db"),
        )


class TempRunStorage(RunStorage):
    """Ephemeral storage backed by a temporary directory."""

    def __init__(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="shogiarena-run-")
        run_dir = Path(self._tempdir.name).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(
            run_dir=run_dir,
            persistent=False,
            dashboard_compatible=True,
            _db_factory=SQLiteShogiDBFactory(run_dir / "game.db"),
        )

    def cleanup(self) -> None:
        self._tempdir.cleanup()


__all__ = ["RunStorage", "FilesystemRunStorage", "TempRunStorage"]
