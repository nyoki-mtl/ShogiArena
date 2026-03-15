from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.dashboard.adapters.snapshot_storage import SnapshotStorage
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.interfaces.boundaries.dashboard_serializer import serialize_dashboard_snapshot_payload
from shogiarena._core.interfaces.boundaries.parsers.snapshot import parse_dashboard_snapshot_payload


def _storage(tmp_path: Path) -> SnapshotStorage:
    return SnapshotStorage(
        DashboardState(),
        tmp_path,
        parse_snapshot=parse_dashboard_snapshot_payload,
        serialize_snapshot=serialize_dashboard_snapshot_payload,
    )


def test_store_games_preserves_snapshot_meta_fields(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    stored = storage.store_games(
        {
            "kind": "bulk",
            "revision": 1,
            "base_revision": None,
            "rows": [{"game_id": "g-1"}],
            "snapshotMeta": {
                "total_games": 10,
                "completed_games": 3,
                "running_games": 2,
                "pending_games": 5,
                "seed": "1234",
                "session_state": "running",
            },
        }
    )

    assert stored is not None
    meta = stored["snapshotMeta"]
    assert meta.get("total_games") == 10
    assert meta.get("completed_games") == 3
    assert meta.get("running_games") == 2
    assert meta.get("pending_games") == 5
    assert meta.get("seed") == "1234"
    assert meta.get("session_state") == "running"


def test_compute_games_delta_preserves_snapshot_meta_fields(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    delta = storage.compute_games_delta(
        previous={"rows": [{"game_id": "g-old"}]},
        rows=[{"game_id": "g-new"}],
        snapshot_meta={
            "total_games": 12,
            "completed_games": 4,
            "running_games": 1,
            "pending_games": 7,
            "is_running": True,
            "session_state": "waiting",
        },
        revision=4,
        base_revision=3,
    )

    assert delta["kind"] == "delta"
    assert delta["revision"] == 4
    assert delta["base_revision"] == 3
    meta = delta["snapshotMeta"]
    assert meta.get("total_games") == 12
    assert meta.get("completed_games") == 4
    assert meta.get("running_games") == 1
    assert meta.get("pending_games") == 7
    assert meta.get("is_running") is True
    assert meta.get("session_state") == "waiting"
