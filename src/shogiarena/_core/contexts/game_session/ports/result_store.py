"""Persistence contracts for finalized run results."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


class PersistedRunResultPort(Protocol):
    """Minimal finalized-run payload consumed by result stores."""

    run_id: str
    run_dir: Path
    summary: Mapping[str, JsonValue] | None
    config_snapshot: Mapping[str, JsonValue] | None
    started_at: datetime | None
    completed_at: datetime | None

    def duration_ms(self) -> int | None: ...

    def serialize_extras(self) -> JsonObject: ...


class ResultStorePort(Protocol):
    """Persist finalized run results."""

    def save_result(self, result: PersistedRunResultPort) -> None: ...


__all__ = ["PersistedRunResultPort", "ResultStorePort"]
