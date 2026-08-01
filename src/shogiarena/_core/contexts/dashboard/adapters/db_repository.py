"""Shared repository-opening helpers for dashboard adapters."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.platform.db.store.repository import ShogiRepositoryPort
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def open_dashboard_repository(db_path: Path, *, immutable: bool = False) -> ShogiRepositoryPort | None:
    """Open an existing dashboard game repository in strict read-only mode."""

    if not db_path.exists():
        return None
    return SQLiteShogiDBFactory(db_path, read_only=True, immutable=immutable).create()


__all__ = ["open_dashboard_repository"]
