"""Shared repository-opening helpers for dashboard adapters."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.platform.db.store.repository import ShogiRepositoryPort
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def open_dashboard_repository(db_path: Path) -> ShogiRepositoryPort | None:
    """Open the dashboard game repository and ensure the schema exists."""

    if not db_path.exists():
        return None
    repository = SQLiteShogiDBFactory(db_path).create()
    repository.create_tables()
    return repository


__all__ = ["open_dashboard_repository"]
