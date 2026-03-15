"""Worker/game stream mediation for dashboard websocket streams."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from shogiarena._core.contexts.dashboard.application.assignment_service import AssignmentService
from shogiarena._core.contexts.dashboard.application.game.state import GameStateUpdater
from shogiarena._core.contexts.dashboard.application.publish_fn import PublishFn
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.contexts.dashboard.application.stream_topics import (
    parse_live_game_gid,
    topic_live_game_snapshot,
)
from shogiarena._core.contexts.dashboard.ports.progress_converters import WorkerSnapshotNormalizer
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

from .stream_diffs.builder import build_game_diff_envelopes

logger = logging.getLogger(__name__)


class WorkerStreamMediator:
    """Coordinate worker update ingestion and live.game stream publication."""

    def __init__(
        self,
        *,
        state: DashboardState,
        game_state: GameStateUpdater,
        assignment: AssignmentService,
        publish: PublishFn,
        min_clock_publish_interval_ms: int = 200,
        normalize_snapshot: WorkerSnapshotNormalizer | None = None,
    ) -> None:
        self._state = state
        self._game_state = game_state
        self._assignment = assignment
        self._publish = publish
        self._min_clock_publish_interval_ms = min_clock_publish_interval_ms
        self._normalize_snapshot = normalize_snapshot
        self._last_clock_publish_at: dict[str, float] = {}
        self._last_engine_state_signature: dict[str, tuple[str, str]] = {}
        self._next_move_seq_by_gid: dict[str, int] = {}

    @staticmethod
    def extract_gid_from_topic(topic: str) -> str | None:
        """Extract game id from websocket topic."""
        return parse_live_game_gid(topic)

    @staticmethod
    def _extract_gid(payload: Mapping[str, object] | GameSnapshot) -> str | None:
        for key in ("gid", "game_id", "gameId"):
            value = json_serialize(payload.get(key))
            if s := coerce_str(value):
                return s
            if (n := coerce_int(value)) is not None:
                return str(n)
        return None

    @staticmethod
    def _effective_game_epoch(*, assignment_rev: int, snapshot: Mapping[str, object] | None = None) -> int:
        if snapshot is not None:
            epoch_raw = snapshot.get("game_epoch")
            epoch = coerce_int(epoch_raw)
            if epoch is not None and epoch >= 0:
                return epoch
        return max(0, assignment_rev)

    @staticmethod
    def _ensure_snapshot_epoch(snapshot: JsonObject, *, assignment_rev: int) -> JsonObject:
        epoch = WorkerStreamMediator._effective_game_epoch(assignment_rev=assignment_rev, snapshot=snapshot)
        snapshot["game_epoch"] = epoch
        return snapshot

    def _seed_move_seq_from_snapshot(self, gid: str, snapshot: Mapping[str, object] | None) -> None:
        if snapshot is None:
            return
        current_ply = coerce_int(snapshot.get("current_ply")) or 0
        if current_ply <= 0:
            return
        previous = self._next_move_seq_by_gid.get(gid, 0)
        if current_ply > previous:
            self._next_move_seq_by_gid[gid] = current_ply

    def _normalize_worker_snapshot(self, snapshot: Mapping[str, object] | GameSnapshot) -> GameSnapshot:
        snapshot_json = to_json_object(snapshot)
        if self._normalize_snapshot is not None:
            try:
                normalized = self._normalize_snapshot(snapshot_json)
            except ValueError:
                normalized = None
            if normalized is not None:
                return normalized
        gid = WorkerStreamMediator._extract_gid(snapshot_json) or ""
        initial_sfen = coerce_str(snapshot_json.get("initial_sfen")) or "startpos"
        return {
            "game_id": gid,
            "initial_sfen": initial_sfen,
            "black_name": coerce_str(snapshot_json.get("black_name")) or "",
            "white_name": coerce_str(snapshot_json.get("white_name")) or "",
            "moves": [],
            "ki2_moves": [],
            "eval_black": [],
            "eval_white": [],
            "nodes_values": [],
            "depth_values": [],
            "seldepth_values": [],
            "move_times_ms": [],
            "wall_times_ms": [],
            "latency_deltas_ms": [],
            "latency_alerts": [],
            "current_ply": 0,
            "sfen": coerce_str(snapshot_json.get("sfen")) or initial_sfen,
        }

    def build_bootstrap_game_snapshot_messages(self, gids: Sequence[object]) -> list[tuple[str, JsonObject]]:
        """Build per-game snapshot messages for assignment bootstrap gids."""
        messages: list[tuple[str, JsonObject]] = []
        assignment_rev = self._assignment.current_revision()
        for gid in gids:
            if not isinstance(gid, str) or not gid.strip():
                continue
            gid_str = gid.strip()
            ws_snapshot = self._game_state.build_ws_snapshot_from_cache(
                gid_str,
                assignment_rev=assignment_rev,
            )
            if ws_snapshot is None:
                continue
            normalized = self._ensure_snapshot_epoch(ws_snapshot, assignment_rev=assignment_rev)
            self._seed_move_seq_from_snapshot(gid_str, normalized)
            messages.append((topic_live_game_snapshot(gid_str), {"gid": gid_str, "snapshot": normalized}))
        return messages

    def resolve_snapshot_from_topic(self, topic: str) -> tuple[str, JsonObject] | None:
        """Resolve websocket snapshot payload for live.game topic."""
        gid = self.extract_gid_from_topic(topic)
        if not gid:
            return None
        ws_snapshot = self._game_state.build_ws_snapshot_from_cache(
            gid,
            assignment_rev=self._assignment.current_revision(),
        )
        if ws_snapshot is None:
            return None
        assignment_rev = self._assignment.current_revision()
        normalized = self._ensure_snapshot_epoch(ws_snapshot, assignment_rev=assignment_rev)
        self._seed_move_seq_from_snapshot(gid, normalized)
        return (topic_live_game_snapshot(gid), {"gid": gid, "snapshot": normalized})

    def worker_update(self, worker_idx: int, payload: Mapping[str, object]) -> None:
        """Ingest one worker update and publish derived game diff streams."""
        payload_json = to_json_object(payload)
        gid = self._extract_gid(payload_json) or self._state.get_worker_assignment(worker_idx)
        event_type = coerce_str(payload_json.get("type")) or ""
        if gid:
            worker_snapshot = self._state.get_worker_snapshot(worker_idx)
            payload_ply_raw = payload_json.get("current_ply")
            payload_ply = coerce_int(payload_ply_raw) or 0
            payload_ply = max(0, payload_ply)

            if worker_snapshot is not None and self._extract_gid(worker_snapshot) == gid:
                ws_ply = max(0, worker_snapshot.get("current_ply", 0))
                ws_moves_len = len(worker_snapshot["moves"])
                has_move_delta = payload_json.get("move") is not None or payload_json.get("ki2_move") is not None

                if has_move_delta and payload_ply > max(ws_ply, ws_moves_len):
                    self._game_state.update_from_worker(gid, payload_json)
                else:
                    self._game_state.cache_snapshot(gid, self._normalize_worker_snapshot(worker_snapshot))
            else:
                self._game_state.update_from_worker(gid, payload_json)
            self._assignment.update_worker_assignment(worker_idx, gid)

        diff_envelopes = self._build_game_diff_envelopes(worker_idx, payload_json)
        if event_type == "move_progress" and not any(topic.endswith(".moves.diff") for topic, _ in diff_envelopes):
            logger.warning(
                "LIVE move_progress produced no moves.diff worker=%s gid=%s payload_keys=%s",
                worker_idx,
                gid or "-",
                sorted(payload_json.keys()),
            )
        for topic, diff_payload in diff_envelopes:
            self._publish(topic, diff_payload, worker_idx=worker_idx)

    def set_worker(
        self,
        worker_idx: int,
        snapshot: Mapping[str, object],
        *,
        should_broadcast: bool = True,
    ) -> None:
        """Set worker snapshot and optionally publish live.game snapshot."""
        worker_snapshot = self._normalize_worker_snapshot(snapshot)
        self._state.set_worker_snapshot(worker_idx, worker_snapshot)
        gid = self._extract_gid(worker_snapshot)
        if gid:
            self._game_state.cache_snapshot(gid, worker_snapshot)
        if should_broadcast and gid:
            ws_snapshot = self._game_state.build_ws_snapshot_from_cache(
                gid,
                assignment_rev=self._assignment.current_revision(),
            )
            if ws_snapshot is not None:
                normalized = self._ensure_snapshot_epoch(
                    ws_snapshot,
                    assignment_rev=self._assignment.current_revision(),
                )
                self._seed_move_seq_from_snapshot(gid, normalized)
                self._publish(
                    topic_live_game_snapshot(gid),
                    {"gid": gid, "snapshot": normalized},
                    worker_idx=worker_idx,
                )

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, object]) -> None:
        """Assign worker->gid and publish assignment + live.game snapshot in order."""
        worker_snapshot = self._normalize_worker_snapshot(snapshot)
        gid = self._extract_gid(worker_snapshot)
        if not gid:
            return
        self._assignment.update_worker_assignment(worker_idx, gid)

        cleaned_snapshot = self._normalize_worker_snapshot(
            {key: value for key, value in worker_snapshot.items() if not str(key).startswith("_")}
        )
        self._state.set_worker_snapshot(worker_idx, cleaned_snapshot)
        self._game_state.cache_snapshot(gid, cleaned_snapshot)
        ws_snapshot = self._game_state.build_ws_snapshot_from_cache(
            gid,
            assignment_rev=self._assignment.current_revision(),
        )
        if ws_snapshot is None:
            return
        normalized = self._ensure_snapshot_epoch(
            ws_snapshot,
            assignment_rev=self._assignment.current_revision(),
        )
        self._seed_move_seq_from_snapshot(gid, normalized)
        self._publish(
            topic_live_game_snapshot(gid),
            {"gid": gid, "snapshot": normalized},
            worker_idx=worker_idx,
        )

    def _build_game_diff_envelopes(
        self,
        worker_idx: int,
        payload: JsonObject,
    ) -> list[tuple[str, JsonObject]]:
        return build_game_diff_envelopes(
            worker_idx=worker_idx,
            payload=payload,
            extract_gid=self._extract_gid,
            assignment_rev=self._assignment.current_revision(),
            next_move_seq_by_gid=self._next_move_seq_by_gid,
            get_worker_snapshot=self._state.get_worker_snapshot,
            min_clock_publish_interval_ms=self._min_clock_publish_interval_ms,
            last_clock_publish_at=self._last_clock_publish_at,
            last_engine_state_signature=self._last_engine_state_signature,
        )
