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
