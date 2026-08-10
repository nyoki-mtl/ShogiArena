from __future__ import annotations

import json
from pathlib import Path

from shogiarena._core.contexts.dashboard.application.archived_schedule import build_archived_schedule_snapshot


class _GameQueryStub:
    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
    ) -> dict[str, object]:
        assert db_path.name == "game.db"
        assert limit == 2
        assert offset == 0
        assert search_query is None
        return {
            "games": [
                {
                    "game_id": "g1",
                    "black_player": "Engine A",
                    "white_player": "Engine B",
                    "game_result": "black_win",
                    "total_plies": 42,
                    "end_time": "2026-08-02T00:00:00+00:00",
                }
            ],
            "total": 1,
        }


class _DbOnlyGameQueryStub:
    def build_games_raw_payload(
        self,
        db_path: Path,
        *,
        limit: int,
        offset: int,
        search_query: str | None,
    ) -> dict[str, object]:
        assert db_path.name == "game.db"
        assert offset == 0
        assert search_query is None
        games = [
            {
                "game_id": "csa_1111111111111111",
                "server_game_id": "wdoor+floodgate+Engine-A+Engine-B+20260810000000",
                "black_player": "Engine A",
                "white_player": "Engine B",
                "game_result": "BLACK_WIN",
                "total_plies": 77,
                "start_time": "2026-08-10T00:00:00+00:00",
                "end_time": "2026-08-10T00:20:00+00:00",
            },
            {
                "game_id": "csa_2222222222222222",
                "server_game_id": "wdoor+floodgate+Engine-C+Engine-D+20260810010000",
                "black_player": "Engine C",
                "white_player": "Engine D",
                "game_result": "WHITE_WIN",
                "total_plies": 88,
                "start_time": "2026-08-10T01:00:00+00:00",
                "end_time": "2026-08-10T01:20:00+00:00",
            },
        ]
        return {"games": games[:limit], "total": len(games)}


def test_build_archived_schedule_snapshot_merges_completed_games(tmp_path: Path) -> None:
    (tmp_path / "schedule.json").write_text(
        json.dumps(
            {
                "games": [
                    {
                        "game_id": "g1",
                        "black": "Engine A",
                        "white": "Engine B",
                        "display_order": 1,
                        "sfen": "startpos",
                    },
                    {
                        "game_id": "g2",
                        "black": "Engine B",
                        "white": "Engine A",
                        "display_order": 2,
                        "sfen": "startpos",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "state.json").write_text(
        json.dumps({"original_total_games": 2, "completed_games_count": 1}),
        encoding="utf-8",
    )

    payload = build_archived_schedule_snapshot(
        tmp_path,
        db_path=tmp_path / "game.db",
        game_query=_GameQueryStub(),  # type: ignore[arg-type]
    )

    assert payload is not None
    assert payload["completed_games"] == 1
    assert payload["pending_games"] == 1
    assert payload["total_games"] == 2
    rows = payload["schedule"]
    assert isinstance(rows, list)
    assert rows[0]["status"] == "completed"
    assert rows[0]["order"] == 0
    assert rows[0]["game_result"] == "black_win"
    assert rows[1]["status"] == "pending"


def test_build_archived_schedule_snapshot_can_project_csa_games_without_schedule_json(tmp_path: Path) -> None:
    payload = build_archived_schedule_snapshot(
        tmp_path,
        db_path=tmp_path / "game.db",
        game_query=_DbOnlyGameQueryStub(),  # type: ignore[arg-type]
        allow_db_only=True,
    )

    assert payload is not None
    assert payload["completed_games"] == 2
    assert payload["pending_games"] == 0
    rows = payload["schedule"]
    assert isinstance(rows, list)
    assert [row["game_id"] for row in rows] == ["csa_2222222222222222", "csa_1111111111111111"]
    assert rows[0]["server_game_id"] == "wdoor+floodgate+Engine-C+Engine-D+20260810010000"
    assert rows[0]["start_time"] == "2026-08-10T01:00:00+00:00"
    assert rows[0]["end_time"] == "2026-08-10T01:20:00+00:00"
    assert rows[0]["black"] == "Engine C"
    assert rows[0]["status"] == "completed"


def test_build_archived_schedule_snapshot_does_not_use_db_only_fallback_by_default(tmp_path: Path) -> None:
    payload = build_archived_schedule_snapshot(
        tmp_path,
        db_path=tmp_path / "game.db",
        game_query=_DbOnlyGameQueryStub(),  # type: ignore[arg-type]
    )

    assert payload is None
