"""Dashboard event contracts for application-service based coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject


class DashboardEventType:
    """Canonical event kind identifiers for dashboard workflows."""

    WORKER_UPDATE = "dashboard.worker_update"
    ENGINE_IO = "dashboard.engine_io"
    CLEAR_ENGINE_LOGS = "dashboard.clear_engine_logs"
    SUMMARY_UPDATE = "dashboard.summary_update"
    GAMES_SNAPSHOT = "dashboard.games_snapshot"
    ASSIGNMENT_STREAM_PUBLISH = "dashboard.assignment_stream_publish"
    SET_WORKER_SNAPSHOT = "dashboard.set_worker_snapshot"
    ASSIGN_WORKER_SNAPSHOT = "dashboard.assign_worker_snapshot"
    UPDATE_ENGINE_OPTIONS = "dashboard.update_engine_options"


class GamesSnapshotPayload(TypedDict):
    """Envelope for games list snapshots."""

    kind: str
    revision: int
    base_revision: int | None
    rows: list[JsonObject]
    snapshotMeta: JsonObject


@dataclass(frozen=True)
class DashboardEvent:
    """Base event passed through in-process dashboard event bus."""

    event_type: str
    payload: JsonObject
    worker_idx: int | None = None
    source: str | None = None
    event_type_hint: str | None = None
    should_broadcast: bool = True


__all__ = ["DashboardEvent", "DashboardEventType", "GamesSnapshotPayload"]
