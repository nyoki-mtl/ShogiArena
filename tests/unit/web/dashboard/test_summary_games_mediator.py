from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.contexts.dashboard.adapters.snapshot_storage import SnapshotStorage
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.application.summary_games_mediator import SummaryGamesMediator
from shogiarena._core.interfaces.boundaries.dashboard_serializer import serialize_dashboard_snapshot_payload
from shogiarena._core.interfaces.boundaries.parsers.snapshot import parse_dashboard_snapshot_payload
from shogiarena._core.shared.kernel.json_types import JsonObject


def _build_mediator(tmp_path: Path) -> tuple[SummaryGamesMediator, list[tuple[str, JsonObject]], DashboardState]:
    state = DashboardState()
    storage = SnapshotStorage(
        state,
        tmp_path,
        parse_snapshot=parse_dashboard_snapshot_payload,
        serialize_snapshot=serialize_dashboard_snapshot_payload,
    )
    published: list[tuple[str, JsonObject]] = []

    def publish(topic: str, payload: JsonObject, *, worker_idx: int | None = None) -> None:
        del worker_idx
        published.append((topic, payload))

    mediator = SummaryGamesMediator(
        state=state,
        storage=storage,
        publish=publish,
    )
    return mediator, published, state


def test_publish_games_snapshot_emits_bulk_with_initial_revision(tmp_path: Path) -> None:
    mediator, published, state = _build_mediator(tmp_path)

    mediator.publish_games_snapshot(
        {
            "schedule": [{"game_id": "g1", "status": "running"}],
            "total_games": 1,
            "completed_games": 0,
        }
    )

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "live.games.delta"
    assert payload["kind"] == "bulk"
    assert payload["revision"] == 1
    assert payload["base_revision"] is None
    assert payload["snapshot_meta"]["total_games"] == 1

    stored = state.get_games_snapshot()
    assert stored is not None
    assert stored["kind"] == "bulk"
    assert stored["revision"] == 1


def test_publish_games_snapshot_emits_delta_on_next_revision(tmp_path: Path) -> None:
    mediator, published, _state = _build_mediator(tmp_path)

    mediator.publish_games_snapshot(
        {
            "schedule": [{"game_id": "g1", "status": "running", "moves": 0}],
            "total_games": 1,
            "completed_games": 0,
        }
    )
    mediator.publish_games_snapshot(
        {
            "schedule": [{"game_id": "g1", "status": "completed", "moves": 132}],
            "total_games": 1,
            "completed_games": 1,
        }
    )

    assert len(published) == 2
    topic, payload = published[1]
    assert topic == "live.games.delta"
    assert payload["kind"] == "delta"
    assert payload["revision"] == 2
    assert payload["base_revision"] == 1
    assert isinstance(payload["rows"], list)
    assert payload["rows"]


def test_publish_games_snapshot_ignores_unsupported_event_type(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mediator, published, state = _build_mediator(tmp_path)

    with caplog.at_level("WARNING", logger="shogiarena._core.contexts.dashboard.application.summary_games_mediator"):
        mediator.publish_games_snapshot({"schedule": []}, event_type="delta")

    assert not published
    assert state.get_games_snapshot() is None
    assert "Ignoring unsupported games snapshot event_type" in caplog.text


def test_terminal_summary_bypasses_publish_coalescing(tmp_path: Path) -> None:
    mediator, published, state = _build_mediator(tmp_path)

    mediator.publish_summary_update(
        {"games": {"completed": 7, "total": 8, "cancelled": 0}},
        source="tournament",
    )
    mediator.publish_summary_update(
        {"games": {"completed": 8, "total": 8, "cancelled": 0}},
        source="tournament",
    )
    mediator.publish_summary_update({"tournament_ended": True}, source="tournament")

    assert len(published) == 2
    topic, terminal = published[-1]
    assert topic == "live.summary.snapshot.tournament"
    assert terminal["games"] == {"completed": 8, "total": 8, "cancelled": 0}
    assert terminal["tournament_ended"] is True
    assert state.get_summary_snapshot("tournament") == terminal


def test_csa_lifecycle_summaries_are_not_dropped_by_coalescing(tmp_path: Path) -> None:
    mediator, published, state = _build_mediator(tmp_path)

    mediator.publish_summary_update(
        {
            "games": {"completed": 0, "total": 1, "cancelled": 0},
            "csa_runs": [{"run_id": "run-1", "phase": "playing", "current_game_id": "game-1"}],
        },
        source="csa",
    )
    mediator.publish_summary_update(
        {
            "games": {"completed": 1, "total": 1, "cancelled": 0},
            "csa_runs": [{"run_id": "run-1", "phase": "idle", "current_game_id": None}],
        },
        source="csa",
    )

    assert len(published) == 2
    topic, idle = published[-1]
    assert topic == "live.summary.snapshot.csa"
    assert idle["csa_runs"] == [{"run_id": "run-1", "phase": "idle", "current_game_id": None}]
    assert state.get_summary_snapshot("csa") == idle
