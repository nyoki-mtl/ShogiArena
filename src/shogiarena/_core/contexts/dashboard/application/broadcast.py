"""WebSocket broadcast handler for Arena Dashboard.

Handles broadcasting updates to connected clients via WebSocket.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from shogiarena._core.contexts.dashboard.application.assignment_service import AssignmentService
from shogiarena._core.contexts.dashboard.application.engine_io_mediator import EngineIoMediator
from shogiarena._core.contexts.dashboard.application.game.state import GameStateUpdater
from shogiarena._core.contexts.dashboard.application.publish_fn import PublishFn
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.application.summary_games_mediator import SummaryGamesMediator
from shogiarena._core.contexts.dashboard.application.worker.stream_mediator import WorkerStreamMediator
from shogiarena._core.contexts.dashboard.ports.progress_converters import WorkerSnapshotNormalizer
from shogiarena._core.contexts.dashboard.ports.snapshot_storage import SnapshotStoragePort as SnapshotStorage
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)

ENGINE_IO_LOG_LIMIT = 1000


class BroadcastHandler:
    """Handles WebSocket broadcast operations.

    Manages broadcasting worker updates, summary updates, games updates,
    and other events to connected WebSocket clients.

    Args:
        state: Shared dashboard state container.
        game_state: Game state updater instance.
        snapshot_storage: Snapshot storage instance.
        publish: Callback to publish messages to WebSocket hub.
        spsa_notifier: Optional callback to notify SPSA API of summary updates.
    """

    def __init__(
        self,
        state: DashboardState,
        game_state: GameStateUpdater,
        snapshot_storage: SnapshotStorage,
        publish: PublishFn,
        spsa_notifier: Callable[[JsonObject], None] | None = None,
        publish_assignment: PublishFn | None = None,
        normalize_snapshot: WorkerSnapshotNormalizer | None = None,
    ) -> None:
        self._state = state
        self._assignment = AssignmentService(state=state, publish=publish_assignment or publish)
        self._summary_games = SummaryGamesMediator(
            state=state,
            storage=snapshot_storage,
            publish=publish,
            spsa_notifier=spsa_notifier,
        )
        self._worker_streams = WorkerStreamMediator(
            state=state,
            game_state=game_state,
            assignment=self._assignment,
            publish=publish,
            normalize_snapshot=normalize_snapshot,
        )
        self._engine_io = EngineIoMediator(
            state=state,
            log_limit=ENGINE_IO_LOG_LIMIT,
        )

    def build_ws_bootstrap_messages(self, worker_filter: set[int] | None = None) -> list[tuple[str, JsonObject]]:
        """Build initial WebSocket payloads for a newly subscribed client."""
        messages: list[tuple[str, JsonObject]] = []
        games_snapshot = self._state.get_games_snapshot()
        if games_snapshot is not None:
            messages.append(("live.games.delta", to_json_object(games_snapshot)))

        assignment_snapshot = self._assignment.build_snapshot(worker_filter=worker_filter)
        if assignment_snapshot is not None:
            messages.append(("live.assignment.snapshot", to_json_object(assignment_snapshot)))
            gids = assignment_snapshot.get("gids")
            if isinstance(gids, list):
                messages.extend(self._worker_streams.build_bootstrap_game_snapshot_messages(gids))
        return messages

    def resolve_ws_snapshot(self, topic: str) -> list[tuple[str, JsonObject]]:
        """Resolve fallback payload for a websocket topic."""
        if topic.startswith("live.summary.snapshot."):
            source = topic.split(".", 3)[-1]
            summary_snapshot = self._state.get_summary_snapshot(source=source)
            if not isinstance(summary_snapshot, Mapping):
                return []
            return [(topic, to_json_object(summary_snapshot))]

        if topic == "live.games.delta":
            snapshot = self._state.get_games_snapshot()
            if snapshot is None:
                return []
            return [("live.games.delta", to_json_object(snapshot))]

        if topic == "live.assignment.snapshot":
            assignment_snapshot = self._assignment.build_snapshot(worker_filter=None)
            if assignment_snapshot is None:
                return []
            return [("live.assignment.snapshot", to_json_object(assignment_snapshot))]

        if topic.startswith("live.engine."):
            snapshot = self._engine_io.resolve_snapshot_from_topic(topic)
            return [snapshot] if snapshot is not None else []

        if topic.startswith("live.game."):
            snapshot = self._worker_streams.resolve_snapshot_from_topic(topic)
            return [snapshot] if snapshot is not None else []

        return []

    def worker_update(self, worker_idx: int, payload: JsonObject) -> None:
        """Broadcast a worker update.

        Args:
            worker_idx: Worker index.
            payload: Update payload.
        """
        self._worker_streams.worker_update(worker_idx, payload)

    def summary_update(self, payload: JsonObject, *, source: str = "tournament") -> None:
        """Broadcast a summary update.

        Args:
            payload: Summary update payload.
            source: Summary source key.
        """
        self._summary_games.publish_summary_update(payload, source=source)

    def games_snapshot(self, snapshot: Mapping[str, JsonValue], *, event_type: str = "bulk") -> None:
        """Broadcast a games list snapshot.

        Args:
            snapshot: Games snapshot payload.
            event_type: Type of event (default: "bulk").
        """
        self._summary_games.publish_games_snapshot(snapshot, event_type=event_type)

    def set_worker(
        self,
        worker_idx: int,
        snapshot: Mapping[str, JsonValue],
        *,
        should_broadcast: bool = True,
    ) -> None:
        """Set a worker snapshot.

        Args:
            worker_idx: Worker index.
            snapshot: Snapshot data.
            should_broadcast: Whether to broadcast the snapshot.
        """
        self._worker_streams.set_worker(worker_idx, snapshot, should_broadcast=should_broadcast)

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, JsonValue]) -> None:
        """Assign a worker to a game and publish assignment + game snapshot in order."""
        self._worker_streams.assign_worker_snapshot(worker_idx, snapshot)
