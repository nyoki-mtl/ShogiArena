from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from shogiarena._core.contexts.dashboard.adapters.spsa import game_listing_service
from shogiarena._core.contexts.dashboard.adapters.spsa.game_listing_service import SpsaGameListingService
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import DashboardSpsaUpdateQueryPort
from shogiarena._core.shared.kernel.json_types import JsonObject


class _LedgerGameQuery:
    def __init__(self, count: int) -> None:
        self._entries = [(f"game-{idx}", idx) for idx in range(count)]
        self.batch_requests: list[list[str]] = []

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        return list(self._entries)

    def get_game_event_snapshots(self, game_ids: list[str]) -> dict[str, JsonObject]:
        self.batch_requests.append(list(game_ids))
        return {
            game_id: {
                "game_id": game_id,
                "black_player": "needle" if game_id == "game-42" else "black",
                "white_player": "white",
                "winner": 2,
            }
            for game_id in game_ids
        }

    def get_game_event_snapshot(self, _game_id: str) -> JsonObject | None:
        raise AssertionError("game listing must use the batch snapshot API")


def _service(tmp_path: Path, query: _LedgerGameQuery) -> SpsaGameListingService:
    return SpsaGameListingService(
        db_path=tmp_path / "missing-game.db",
        update_query_service=cast(DashboardSpsaUpdateQueryPort, query),
        read_only=True,
    )


def test_fallback_paginates_before_batch_snapshot_hydration(tmp_path: Path) -> None:
    query = _LedgerGameQuery(100)

    games, total = _service(tmp_path, query).list_games(offset=10, limit=5, search_query="")

    assert total == 100
    assert [game["game_id"] for game in games] == [
        "game-89",
        "game-88",
        "game-87",
        "game-86",
        "game-85",
    ]
    assert query.batch_requests == [["game-89", "game-88", "game-87", "game-86", "game-85"]]


def test_fallback_search_uses_one_batch_snapshot(tmp_path: Path) -> None:
    query = _LedgerGameQuery(100)

    games, total = _service(tmp_path, query).list_games(offset=0, limit=10, search_query="needle")

    assert total == 1
    assert [game["game_id"] for game in games] == ["game-42"]
    assert len(query.batch_requests) == 1
    assert len(query.batch_requests[0]) == 100


def test_database_page_uses_one_batch_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[tuple[object, ...]] = [
        (f"game-{idx}", "black", "white", "DRAW", 10, now, "", "", "") for idx in range(50)
    ]

    class _Result:
        def scalar_one(self) -> int:
            return len(rows)

        def all(self) -> list[tuple[object, ...]]:
            return rows

    class _Session:
        def execute(self, _statement: object) -> _Result:
            return _Result()

    class _Repository:
        session = _Session()

        def close_db(self) -> None:
            return

    monkeypatch.setattr(
        game_listing_service,
        "open_dashboard_repository",
        lambda *_args, **_kwargs: _Repository(),
    )
    query = _LedgerGameQuery(50)

    games, total = _service(tmp_path, query).list_games(offset=0, limit=50, search_query="")

    assert total == 50
    assert len(games) == 50
    assert len(query.batch_requests) == 1
    assert len(query.batch_requests[0]) == 50
