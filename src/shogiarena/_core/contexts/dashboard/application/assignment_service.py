"""Assignment snapshot management for dashboard websocket streams."""

from __future__ import annotations

import time

from shogiarena._core.contexts.dashboard.application.publish_fn import PublishFn
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str


class AssignmentService:
    """Manage worker assignments and assignment stream publication."""

    def __init__(self, state: DashboardState, publish: PublishFn) -> None:
        self._state = state
        self._publish = publish

    def current_revision(self) -> int:
        """Return current assignment revision."""
        return self._state.get_assignment_rev()

    def build_snapshot(self, *, worker_filter: set[int] | None) -> JsonObject:
        """Build an assignment snapshot payload."""
        snapshot_assignments: dict[str, str | None] = {}
        gids: list[str] = []
        seen: set[str] = set()
        assignments = self._state.get_worker_assignments()
        indices = sorted(assignments.keys()) if assignments else []
        for idx in indices:
            if worker_filter is not None and idx not in worker_filter:
                continue
            gid = assignments.get(idx)
            snapshot_assignments[str(idx)] = gid
            gid_str = coerce_str(gid)
            if gid_str and gid_str not in seen:
                seen.add(gid_str)
                gids.append(gid_str)
        return to_json_object(
            {
                "assignments": snapshot_assignments,
                "gids": gids,
                "updated_at": int(time.time() * 1000),
                "assignment_rev": self.current_revision(),
            }
        )

    def publish_snapshot(self) -> None:
        """Publish assignment snapshot stream messages."""
        payload = self.build_snapshot(worker_filter=None)
        self._publish("live.assignment.snapshot", to_json_object(payload), worker_idx=None)

    def update_worker_assignment(self, worker_idx: int, gid: str) -> None:
        """Sync one worker assignment and publish snapshot when changed."""
        prev = self._state.get_worker_assignment(worker_idx)
        if prev == gid:
            return
        self._state.set_worker_assignment(worker_idx, gid)
        self._state.bump_assignment_rev()
        self.publish_snapshot()
