"""Minimal database-facing protocols shared across contexts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

import rshogi
from typing_extensions import TypedDict

from shogiarena._core.shared.kernel.game_results import GameResult


class _GameRecordPlayersRequired(TypedDict):
    black_player: str
    white_player: str
    result: GameResult


class GameRecordPlayers(_GameRecordPlayersRequired, total=False):
    game_id: int
    game_name: str
    initial_sfen: str | None


@runtime_checkable
class DatabaseServicePort(Protocol):
    """Run-scoped database operations required by orchestration."""

    def ensure_schema_compatibility(self) -> None: ...
    def close(self) -> None: ...
    def append_record_list(
        self,
        record_list: Iterable[rshogi.record.GameRecord | None],
        *,
        should_update: bool = False,
    ) -> None: ...
    def get_game_id_by_name(self, game_name: str) -> int | None: ...
    def load_record(
        self,
        *,
        game_id: int | None = None,
        game_name: str | None = None,
    ) -> rshogi.record.GameRecord | None: ...
    def record_game_participation(self, *, game_id: int, participation: Iterable[object]) -> None: ...
    def get_games_with_players(self, *, game_type: str) -> Sequence[GameRecordPlayers]: ...
