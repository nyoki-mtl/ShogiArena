from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.assignment_service import AssignmentService
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.shared.kernel.json_types import JsonObject


def _build_service() -> tuple[AssignmentService, DashboardState, list[tuple[str, JsonObject, int | None]]]:
    state = DashboardState()
    published: list[tuple[str, JsonObject, int | None]] = []

    def publish(topic: str, payload: JsonObject, *, worker_idx: int | None = None) -> None:
        published.append((topic, payload, worker_idx))

    return AssignmentService(state=state, publish=publish), state, published


def test_build_snapshot_filters_workers_and_deduplicates_gids() -> None:
    service, state, _published = _build_service()
    state.set_worker_assignment(2, "g2")
    state.set_worker_assignment(1, "g1")
    state.set_worker_assignment(0, "g1")

    snapshot = service.build_snapshot(worker_filter={0, 2})

    assert snapshot["assignments"] == {"0": "g1", "2": "g2"}
    assert snapshot["gids"] == ["g1", "g2"]
    assert snapshot["assignment_rev"] == 0
    assert isinstance(snapshot["updatedAt"], int)
    assert snapshot["updatedAt"] >= 0


def test_update_worker_assignment_updates_state_and_publishes_streams() -> None:
    service, state, published = _build_service()

    service.update_worker_assignment(4, "g4")

    assert state.get_worker_assignment(4) == "g4"
    assert state.get_assignment_rev() == 1
    assert [topic for topic, _payload, _worker_idx in published] == ["live.assignment.snapshot"]
    snapshot_payload = published[0][1]
    assert snapshot_payload["assignment_rev"] == 1
    assert snapshot_payload["assignments"] == {"4": "g4"}
    assert snapshot_payload["gids"] == ["g4"]
    assert all(worker_idx is None for _topic, _payload, worker_idx in published)


def test_update_worker_assignment_is_noop_when_assignment_is_unchanged() -> None:
    service, state, published = _build_service()
    state.set_worker_assignment(5, "g5")
    state.assignment_rev = 3

    service.update_worker_assignment(5, "g5")

    assert state.get_assignment_rev() == 3
    assert published == []
