"""Port type aliases for cross-context worker snapshot normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

WorkerSnapshotNormalizer = Callable[[Mapping[str, JsonValue]], GameSnapshot | None]


__all__ = ["WorkerSnapshotNormalizer"]
