"""Engine I/O snapshot mediation for dashboard websocket streams."""

from __future__ import annotations

from collections import deque

from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.application.stream_topics import (
    parse_live_engine_io_target,
    topic_live_engine_io_snapshot,
)
from shogiarena._core.shared.kernel.json_types import JsonObject


class EngineIoMediator:
    """Resolve engine I/O stream snapshots from dashboard state."""

    def __init__(self, *, state: DashboardState, log_limit: int = 1000) -> None:
        self._state = state
        self._log_limit = log_limit

    @staticmethod
    def extract_engine_log_topic(topic: str) -> tuple[str, str] | None:
        """Extract (gid, role) from engine log topic."""
        return parse_live_engine_io_target(topic)

    def build_snapshot_payload(self, gid: str, role: str) -> JsonObject:
        """Build engine I/O snapshot payload for one game/role."""
        logs = self._state.get_engine_io_logs(gid)
        role_logs = logs.get(role)
        entries: list[dict[str, object]] = []
        if isinstance(role_logs, deque):
            entries = [dict(entry) for entry in role_logs]
        return {
            "gid": gid,
            "role": role,
            "entries": entries,
            "limit": self._log_limit,
        }

    def resolve_snapshot_from_topic(self, topic: str) -> tuple[str, JsonObject] | None:
        """Resolve websocket snapshot payload for engine I/O topic."""
        parsed = self.extract_engine_log_topic(topic)
        if not parsed:
            return None
        gid, role = parsed
        return (topic_live_engine_io_snapshot(gid, role), self.build_snapshot_payload(gid, role))
