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


def test_store_summary_returns_none_when_games_missing(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    assert (
        storage.store_summary(
            {
                "games": {
                    "completed": 1,
                },
                "summaryReady": True,
            },
            source="tournament",
        )
        is None
    )


def test_store_summary_returns_none_when_games_counts_are_non_numeric(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    assert (
        storage.store_summary(
            {
                "games": {
                    "completed": "1",
                    "total": "2",
                },
                "summaryReady": True,
            },
            source="tournament",
        )
        is None
    )


def test_store_games_returns_none_when_rows_is_not_list(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    assert (
        storage.store_games(
            {
                "kind": "bulk",
                "revision": 1,
                "base_revision": None,
                "rows": {"game_id": "g-1"},
                "snapshotMeta": {},
            }
        )
        is None
    )


def test_store_games_returns_none_when_row_is_not_object(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    assert (
        storage.store_games(
            {
                "kind": "bulk",
                "revision": 1,
                "base_revision": None,
                "rows": ["invalid"],
                "snapshotMeta": {},
            }
        )
        is None
    )


def test_store_games_returns_none_when_snapshot_meta_not_object(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    assert (
        storage.store_games(
            {
                "kind": "bulk",
                "revision": 1,
                "base_revision": None,
                "rows": [{"game_id": "g-1"}],
                "snapshotMeta": 123,
            }
        )
        is None
    )
