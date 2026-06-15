"""Book タブ API ルートの HTTP レベルテスト。Task 0018。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rshogi
from aiohttp.test_utils import make_mocked_request
from rshogi.record import GameResult

from shogiarena._core.interfaces.dashboard.book.api import BookAPI


class _GameQueryStub:
    def __init__(self, games: list[dict[str, object]]) -> None:
        self._games = games

    def load_games(self, db_path: Path, *, game_type: str = "arena") -> list[dict[str, object]]:
        return self._games

    def load_game_record(self, db_path: Path, *, game_name: str) -> rshogi.record.GameRecord | None:
        return None


class _RaisingGameQueryStub:
    def load_games(self, db_path: Path, *, game_type: str = "arena") -> list[dict[str, object]]:
        raise ValueError("game.db is corrupt")


@pytest.mark.asyncio
async def test_book_summary_returns_aggregation_shape(tmp_path: Path) -> None:
    games = [
        {"game_name": "g1", "black_engine": "E1", "white_engine": "E2", "result": GameResult.BLACK_WIN},
        {"game_name": "g2", "black_engine": "E1", "white_engine": "E2", "result": GameResult.WHITE_WIN},
    ]
    api = BookAPI(db_path=tmp_path / "game.db", game_query=_GameQueryStub(games))  # type: ignore[arg-type]
    response = await api.get_summary(make_mocked_request("GET", "/api/book/summary"))
    payload = json.loads(response.text)

    assert response.status == 200
    # participation 無し（load_game_record None）のため book は集計されないが、payload 形は提供される。
    assert payload["book_count"] == 0
    assert payload["books"] == []
    assert "heuristic upper bound" in payload["note"]


@pytest.mark.asyncio
async def test_book_summary_converts_failure_to_500(tmp_path: Path) -> None:
    api = BookAPI(db_path=tmp_path / "game.db", game_query=_RaisingGameQueryStub())  # type: ignore[arg-type]
    response = await api.get_summary(make_mocked_request("GET", "/api/book/summary"))

    assert response.status == 500
