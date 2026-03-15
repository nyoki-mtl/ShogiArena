from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.application.assignment_service import AssignmentService
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.application.worker.stream_mediator import WorkerStreamMediator
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.snapshots import GameSnapshot


class StubGameState:
    def __init__(self) -> None:
        self.updated: list[tuple[str, Mapping[str, JsonValue]]] = []
        self.cached: list[tuple[str, GameSnapshot]] = []
        self.rev_calls: list[tuple[str, int]] = []

    def cache_snapshot(self, gid: str, snapshot: GameSnapshot) -> None:
        self.cached.append((gid, snapshot))

    def update_from_worker(self, gid: str, payload: Mapping[str, JsonValue]) -> None:
        self.updated.append((gid, payload))

    def build_ws_snapshot(self, gid: str, snapshot: GameSnapshot, *, assignment_rev: int = 0) -> JsonObject:
        return {"gid": gid, "assignment_rev": assignment_rev}

    def build_ws_snapshot_from_cache(self, gid: str, *, assignment_rev: int = 0) -> JsonObject:
        self.rev_calls.append((gid, assignment_rev))
        return {"gid": gid, "assignment_rev": assignment_rev}


def _build_mediator() -> tuple[
    WorkerStreamMediator,
    DashboardState,
    StubGameState,
    list[tuple[str, JsonObject, int | None]],
    list[tuple[str, JsonObject, int | None]],
]:
    state = DashboardState()
    game_state = StubGameState()
    published: list[tuple[str, JsonObject, int | None]] = []
    assignment_published: list[tuple[str, JsonObject, int | None]] = []

    def publish(topic: str, payload: JsonObject, *, worker_idx: int | None = None) -> None:
        published.append((topic, payload, worker_idx))

    def publish_assignment(topic: str, payload: JsonObject, *, worker_idx: int | None = None) -> None:
        assignment_published.append((topic, payload, worker_idx))

    assignment = AssignmentService(state=state, publish=publish_assignment)
    mediator = WorkerStreamMediator(
        state=state,
        game_state=game_state,
        assignment=assignment,
        publish=publish,
    )
    return mediator, state, game_state, published, assignment_published


def test_resolve_snapshot_from_topic_uses_current_assignment_revision() -> None:
    mediator, state, game_state, _published, _assignment_published = _build_mediator()
    state.assignment_rev = 7

    resolved = mediator.resolve_snapshot_from_topic("live.game.g7.snapshot")

    assert resolved == (
        "live.game.g7.snapshot",
        {"gid": "g7", "snapshot": {"gid": "g7", "assignment_rev": 7, "game_epoch": 7}},
    )
    assert game_state.rev_calls == [("g7", 7)]


def test_worker_update_syncs_assignment_and_publishes_state_diff() -> None:
    mediator, state, _game_state, published, assignment_published = _build_mediator()

    mediator.worker_update(
        2,
        {
            "gid": "g2",
            "type": "meta",
            "game_result": "BLACK_WIN",
        },
    )

    assert state.get_worker_assignment(2) == "g2"
    assert state.get_assignment_rev() == 1
    assert [topic for topic, _payload, _worker_idx in assignment_published] == ["live.assignment.snapshot"]
    assert (
        "live.game.g2.moves.diff",
        {
            "gid": "g2",
            "assignment_rev": 1,
            "game_epoch": 1,
            "game_result": "BLACK_WIN",
            "move_seq": 1,
        },
        2,
    ) in published
    assert (
        "live.game.g2.meta.diff",
        {
            "gid": "g2",
            "assignment_rev": 1,
            "game_epoch": 1,
            "game_result": "BLACK_WIN",
        },
        2,
    ) in published


def test_set_worker_broadcasts_live_game_snapshot() -> None:
    mediator, state, game_state, published, _assignment_published = _build_mediator()
    state.assignment_rev = 5

    mediator.set_worker(3, {"gid": "g3"}, should_broadcast=True)

    worker_snapshot = state.get_worker_snapshot(3)
    assert worker_snapshot is not None
    assert worker_snapshot.get("game_id") == "g3"
    assert worker_snapshot.get("initial_sfen") == "startpos"
    assert len(game_state.cached) == 1
    cached_gid, cached_snapshot = game_state.cached[0]
    assert cached_gid == "g3"
    assert cached_snapshot.get("game_id") == "g3"
    assert published == [
        (
            "live.game.g3.snapshot",
            {"gid": "g3", "snapshot": {"gid": "g3", "assignment_rev": 5, "game_epoch": 5}},
            3,
        )
    ]
