"""Shared service-port contracts reused across runtime contexts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from shogiarena._core.shared.kernel.database_types import DatabaseServicePort, GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject

ArtifactResolutionPort = Callable[[str, JsonObject | None], Path]
"""Resolve an artifact spec + optional build overrides to a local binary ``Path``."""


class RatingServicePort(Protocol):
    def update_ratings(self, black_player: str, white_player: str, game_result: GameResult) -> tuple[float, float]: ...


class SprtServicePort(Protocol):
    games_played: int

    def is_finished(self) -> bool: ...
    def add_game_result(self, result: GameResult) -> Any: ...
    def get_status(self) -> Any: ...
    def to_snapshot(self) -> Any: ...
    @classmethod
    def from_snapshot(cls, snapshot: Any) -> SprtServicePort: ...


class RunStorageFactoryPort(Protocol):
    def create_run_storage(self, run_dir: Path) -> Any: ...


__all__ = [
    "ArtifactResolutionPort",
    "DatabaseServicePort",
    "GameRecordPlayers",
    "RatingServicePort",
    "RunStorageFactoryPort",
    "SprtServicePort",
]
