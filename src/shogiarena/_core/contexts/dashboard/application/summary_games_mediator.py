"""Summary/games snapshot mediation for dashboard websocket streams."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Mapping

from shogiarena._core.contexts.dashboard.application.events import GamesSnapshotPayload
from shogiarena._core.contexts.dashboard.application.publish_fn import PublishFn
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import SnapshotStoragePort as SnapshotStorage
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject

MIN_SUMMARY_PUBLISH_INTERVAL_MS = 1000
logger = logging.getLogger(__name__)


class SummaryGamesMediator:
    """Coordinate summary/games snapshot storage and websocket publication."""

    def __init__(
        self,
        *,
        state: DashboardState,
        storage: SnapshotStorage,
        publish: PublishFn,
        spsa_notifier: Callable[[JsonObject], None] | None = None,
    ) -> None:
        self._state = state
        self._storage = storage
        self._publish = publish
        self._spsa_notifier = spsa_notifier
        self._last_summary_publish_at: dict[str, float] = {}
        self._last_games_published_snapshot: GamesSnapshotPayload | None = None

    def _next_games_revision(self) -> tuple[int, int | None]:
        previous_snapshot = self._state.get_games_snapshot()
        previous_revision: int | None = None
        if isinstance(previous_snapshot, Mapping):
            raw = previous_snapshot.get("revision")
            if isinstance(raw, int):
                previous_revision = raw
        next_revision = (previous_revision or 0) + 1
        return next_revision, previous_revision

    def publish_summary_update(self, payload: JsonObject, *, source: str) -> None:
        """Store and publish summary updates with coalescing."""
        base_snapshot = copy.deepcopy(self._state.get_summary_snapshot(source) or {})
        snapshot = {**base_snapshot, **payload}
        snapshot["summarySource"] = source

        if "is_summary_ready" not in payload and "summaryReady" not in payload:
            snapshot["is_summary_ready"] = True
        if payload.get("tournament_ended") and "tournamentFinished" not in payload:
            snapshot.setdefault("tournamentFinished", True)

        sanitised = self._storage.store_summary(snapshot, source=source)
        if sanitised is None:
            return
        if self._spsa_notifier is not None and source == "spsa":
            self._spsa_notifier(sanitised)

        now_ms = time.time() * 1000
        last_sent = self._last_summary_publish_at.get(source)
        if last_sent is not None and now_ms - last_sent < MIN_SUMMARY_PUBLISH_INTERVAL_MS:
            return
        self._publish(f"live.summary.snapshot.{source}", to_json_object(sanitised))
        self._last_summary_publish_at[source] = now_ms

    def publish_games_snapshot(self, snapshot: Mapping[str, object], *, event_type: str = "bulk") -> None:
        """Store and publish games snapshots with delta coalescing."""
        if event_type != "bulk":
            logger.warning("Ignoring unsupported games snapshot event_type: %s", event_type)
            return
        base = to_json_object(snapshot)
        next_revision, previous_revision = self._next_games_revision()
        schedule_rows_raw = base.pop("schedule", [])
        schedule_rows: list[JsonObject] = []
        if isinstance(schedule_rows_raw, list):
            for row in schedule_rows_raw:
                if is_str_object_mapping(row):
                    schedule_rows.append(to_json_object(row))
        rows_for_delta: list[JsonObject] = list(schedule_rows)
        event_payload: JsonObject = {
            "kind": "bulk",
            "revision": next_revision,
            "base_revision": previous_revision,
            "rows": list(schedule_rows),
            "snapshotMeta": dict(base),
        }
        sanitised = self._storage.store_games(event_payload)
        if sanitised is None:
            return

        previous = copy.deepcopy(self._last_games_published_snapshot) if self._last_games_published_snapshot else None
        if previous:
            delta = self._storage.compute_games_delta(
                to_json_object(previous),
                rows_for_delta,
                base,
                revision=next_revision,
                base_revision=previous_revision,
            )
            self._publish("live.games.delta", to_json_object(delta))
        else:
            self._publish("live.games.delta", to_json_object(sanitised))
        self._last_games_published_snapshot = copy.deepcopy(sanitised)
