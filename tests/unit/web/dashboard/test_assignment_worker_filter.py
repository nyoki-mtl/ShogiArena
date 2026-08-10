"""The worker filter's effect on assignment snapshots.

This pins the mechanism behind a real outage: the CSA dashboard never showed
live updates for any bridge run that appeared after the server started.

The page built a worker filter from the cards it had, and the server drops
messages for workers outside that filter — including the assignment snapshot,
which is the *only* way the page learns a worker exists. A worker discovered
later was therefore unreachable by construction: it could not be learned about,
so it could not be added to the filter, so it could not be learned about.

The CSA page now sends no filter. These tests fix both halves of the contract
that makes that safe.
"""

from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.assignment_service import AssignmentService
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState


def _service() -> AssignmentService:
    state = DashboardState()
    published: list[tuple[str, object]] = []

    def publish(topic: str, payload: object, **_: object) -> None:
        published.append((topic, payload))

    service = AssignmentService(state, publish)
    service.update_worker_assignment(0, "game-early")
    service.update_worker_assignment(9, "game-discovered-later")
    return service


def test_an_unfiltered_snapshot_carries_every_worker() -> None:
    snapshot = _service().build_snapshot(worker_filter=None)

    assignments = snapshot["assignments"]
    assert isinstance(assignments, dict)
    assert assignments == {"0": "game-early", "9": "game-discovered-later"}
    assert snapshot["gids"] == ["game-early", "game-discovered-later"]


def test_a_filter_hides_the_worker_the_page_would_need_to_learn_about() -> None:
    """The deadlock, stated as a test.

    A page whose filter says {0} is told only about worker 0. Worker 9 is
    invisible to it, so it can never widen the filter to include worker 9.
    """
    snapshot = _service().build_snapshot(worker_filter={0})

    assignments = snapshot["assignments"]
    assert isinstance(assignments, dict)
    assert assignments == {"0": "game-early"}
    assert "9" not in assignments
    assert snapshot["gids"] == ["game-early"]
