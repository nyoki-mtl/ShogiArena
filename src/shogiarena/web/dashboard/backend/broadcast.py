"""WebSocket broadcast handler for Arena Dashboard.

Handles broadcasting updates to connected clients via WebSocket.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from shogiarena.utils.types.coerce import coerce_int, coerce_str, is_strict_numeric
from shogiarena.utils.types.snapshots import GameSnapshot, WorkerSnapshot
from shogiarena.web.dashboard.backend.types import GamesSnapshotPayload

if TYPE_CHECKING:
    from shogiarena.web.dashboard.backend.game_cache import GameSnapshotCache
    from shogiarena.web.dashboard.backend.game_state import GameStateUpdater
    from shogiarena.web.dashboard.backend.snapshot_storage import SnapshotStorage
    from shogiarena.web.dashboard.backend.state_container import DashboardState

logger = logging.getLogger(__name__)

MIN_CLOCK_PUBLISH_INTERVAL_MS = 200
MIN_SUMMARY_PUBLISH_INTERVAL_MS = 1000
MIN_GAMES_PUBLISH_INTERVAL_MS = 1000


class PublishCallback(Protocol):
    """Protocol for WebSocket publish callback."""

    def __call__(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        worker_idx: int | None = None,
    ) -> None:
        """Publish a message to the WebSocket hub."""
        ...


class BroadcastHandler:
    """Handles WebSocket broadcast operations.

    Manages broadcasting worker updates, summary updates, games updates,
    and other events to connected WebSocket clients.

    Args:
        state: Shared dashboard state container.
        game_cache: Game snapshot cache instance.
        game_state: Game state updater instance.
        snapshot_storage: Snapshot storage instance.
        publish: Callback to publish messages to WebSocket hub.
        spsa_notifier: Optional callback to notify SPSA API of summary updates.
    """

    def __init__(
        self,
        state: DashboardState,
        game_cache: GameSnapshotCache,
        game_state: GameStateUpdater,
        snapshot_storage: SnapshotStorage,
        publish: PublishCallback,
        spsa_notifier: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._state = state
        self._cache = game_cache
        self._game_state = game_state
        self._storage = snapshot_storage
        self._publish = publish
        self._spsa_notifier = spsa_notifier
        self._last_clock_publish_at: dict[str, float] = {}
        self._last_summary_publish_at: dict[str, float] = {}
        self._last_games_publish_at: float | None = None
        self._last_games_published_snapshot: GamesSnapshotPayload | None = None

    def worker_update(self, worker_idx: int, payload: dict[str, Any]) -> None:
        """Broadcast a worker update.

        Args:
            worker_idx: Worker index.
            payload: Update payload.
        """
        gid = self._extract_gid(payload) or self._state.worker_assignment.get(worker_idx)
        if gid:
            worker_snapshot = self._state.worker_snapshots.get(worker_idx)
            payload_ply_raw = payload.get("currentPly")
            payload_ply = coerce_int(payload_ply_raw) or 0
            payload_ply = max(0, payload_ply)

            if worker_snapshot is not None and self._extract_gid(worker_snapshot) == gid:
                ws_ply = max(0, worker_snapshot.get("currentPly", 0))
                ws_moves_len = len(worker_snapshot["moves"])
                has_move_delta = payload.get("move") is not None or payload.get("ki2_move") is not None

                if has_move_delta and payload_ply > max(ws_ply, ws_moves_len):
                    self._game_state.update_from_worker(gid, payload)
                else:
                    self._cache.set(gid, dict(worker_snapshot))
            else:
                self._game_state.update_from_worker(gid, payload)
            self._maybe_publish_assignment(worker_idx, gid)

        for topic, diff_payload in self._build_game_diff_envelopes(worker_idx, payload):
            self._publish(topic, diff_payload, worker_idx=worker_idx)

    def summary_update(self, payload: dict[str, Any], *, source: str = "tournament") -> None:
        """Broadcast a summary update.

        Args:
            payload: Summary update payload.
            source: Summary source key.
        """
        base_snapshot = copy.deepcopy(self._state.summary_snapshots.get(source, {}))
        snapshot = {**base_snapshot, **payload}

        snapshot["summarySource"] = source

        if "summaryReady" not in payload:
            snapshot["summaryReady"] = True
        if payload.get("tournament_ended") and "tournamentFinished" not in payload:
            snapshot.setdefault("tournamentFinished", True)

        sanitised = self._storage.store_summary(snapshot, source=source)
        if sanitised is None:
            return
        if self._spsa_notifier is not None and source == "spsa":
            try:
                self._spsa_notifier(sanitised)
            except Exception:
                logger.debug("Failed to publish SPSA summary snapshot", exc_info=True)
        now_ms = time.time() * 1000
        last_sent = self._last_summary_publish_at.get(source)
        if last_sent is not None and now_ms - last_sent < MIN_SUMMARY_PUBLISH_INTERVAL_MS:
            return
        self._publish(f"live.summary.snapshot.{source}", dict(sanitised))
        self._last_summary_publish_at[source] = now_ms

    def games_snapshot(self, snapshot: Mapping[str, Any], *, event_type: str = "bulk") -> None:
        """Broadcast a games list snapshot.

        Args:
            snapshot: Games snapshot payload.
            event_type: Type of event (default: "bulk").
        """
        base = dict(snapshot)
        schedule_rows = base.pop("schedule", [])
        if not isinstance(schedule_rows, list):
            schedule_rows = []
        event_payload: dict[str, Any] = {
            "type": event_type,
            "rows": schedule_rows,
            "snapshotMeta": base,
        }
        sanitised = self._storage.store_games(event_payload)
        if sanitised is None:
            return
        now_ms = time.time() * 1000
        last_sent = self._last_games_publish_at
        if last_sent is not None and now_ms - last_sent < MIN_GAMES_PUBLISH_INTERVAL_MS:
            return
        previous = copy.deepcopy(self._last_games_published_snapshot) if self._last_games_published_snapshot else None
        if previous:
            delta = self._storage.compute_games_delta(previous, schedule_rows, base)
            self._publish("live.games.delta", dict(delta))
        else:
            self._publish("live.games.delta", dict(sanitised))
        self._last_games_publish_at = now_ms
        self._last_games_published_snapshot = copy.deepcopy(sanitised)

    def set_worker(self, worker_idx: int, snapshot: WorkerSnapshot, *, broadcast: bool = True) -> None:
        """Set a worker snapshot.

        Args:
            worker_idx: Worker index.
            snapshot: Snapshot data.
            broadcast: Whether to broadcast the snapshot.
        """
        self._state.worker_snapshots[worker_idx] = cast(WorkerSnapshot, dict(snapshot))
        gid = self._extract_gid(snapshot)
        if gid:
            self._cache.set(gid, dict(snapshot))
        if broadcast and gid:
            ws_snapshot = self._game_state.build_ws_snapshot(
                gid, cast(GameSnapshot, snapshot), assignment_rev=self._state.assignment_rev
            )
            if ws_snapshot is not None:
                self._publish(
                    f"live.game.{gid}.snapshot",
                    {"gid": gid, "snapshot": ws_snapshot},
                    worker_idx=worker_idx,
                )

    def assign_worker_snapshot(self, worker_idx: int, snapshot: WorkerSnapshot) -> None:
        """Assign a worker to a game and publish assignment + game snapshot in order."""
        gid = self._extract_gid(snapshot)
        if not gid:
            return
        prev = self._state.worker_assignment.get(worker_idx)
        if prev != gid:
            self._state.worker_assignment[worker_idx] = gid
            self._state.assignment_rev += 1
            self.publish_assignment_snapshot(worker_idx=worker_idx)

        cleaned_snapshot = {k: v for k, v in snapshot.items() if not str(k).startswith("_")}
        self._state.worker_snapshots[worker_idx] = cast(WorkerSnapshot, cleaned_snapshot)
        self._cache.set(gid, dict(cleaned_snapshot))
        ws_snapshot = self._game_state.build_ws_snapshot(
            gid, cast(GameSnapshot, cleaned_snapshot), assignment_rev=self._state.assignment_rev
        )
        if ws_snapshot is None:
            return
        self._publish(
            f"live.game.{gid}.snapshot",
            {"gid": gid, "snapshot": ws_snapshot},
            worker_idx=worker_idx,
        )

    def publish_assignment_snapshot(self, *, worker_idx: int | None = None) -> None:
        """Publish an assignment snapshot.

        Args:
            worker_idx: Optional worker index that triggered the update.
        """
        payload = self._build_assignment_snapshot(worker_filter=None)
        if payload is None:
            return
        # Publish snapshot as the authoritative stream; keep diff for legacy subscribers.
        self._publish("live.assignment.snapshot", payload, worker_idx=None)
        self._publish("live.assignment.diff", payload, worker_idx=None)

    def _maybe_publish_assignment(self, worker_idx: int, gid: str) -> None:
        """Publish assignment if it changed.

        Args:
            worker_idx: Worker index.
            gid: New game ID.
        """
        prev = self._state.worker_assignment.get(worker_idx)
        if prev == gid:
            return
        self._state.worker_assignment[worker_idx] = gid
        self._state.assignment_rev += 1
        self.publish_assignment_snapshot(worker_idx=worker_idx)

    def _build_assignment_snapshot(self, *, worker_filter: set[int] | None) -> dict[str, Any] | None:
        """Build an assignment snapshot.

        Args:
            worker_filter: Optional filter for worker indices.

        Returns:
            Assignment snapshot or None if no assignments.
        """
        assignments: dict[str, Any] = {}
        gids: list[str] = []
        seen: set[str] = set()
        indices = sorted(self._state.worker_assignment.keys()) if self._state.worker_assignment else []
        for idx in indices:
            if worker_filter is not None and idx not in worker_filter:
                continue
            gid = self._state.worker_assignment.get(idx)
            assignments[str(idx)] = gid
            gid_str = coerce_str(gid)
            if gid_str and gid_str not in seen:
                seen.add(gid_str)
                gids.append(gid_str)
        return {
            "assignments": assignments,
            "gids": gids,
            "updatedAt": int(time.time() * 1000),
            "assignment_rev": self._state.assignment_rev,
        }

    @staticmethod
    def _extract_gid(payload: Mapping[str, Any]) -> str | None:
        """Extract game ID from payload.

        Args:
            payload: Payload to extract from.

        Returns:
            Game ID or None.
        """
        for key in ("gid", "game_id", "gameId"):
            value = payload.get(key)
            if s := coerce_str(value):
                return s
            if (n := coerce_int(value)) is not None:
                return str(n)
        return None

    def _build_game_diff_envelopes(
        self,
        worker_idx: int,
        payload: Mapping[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """Translate worker_update payloads into gid-stream diffs.

        Args:
            worker_idx: Worker index.
            payload: Worker update payload.

        Returns:
            List of (topic, payload) tuples.
        """
        gid = self._extract_gid(payload)
        if not gid:
            return []

        type_value = payload.get("type")
        type_str = coerce_str(type_value)

        analysis_keys = {
            "eval",
            "eval_cp",
            "depth",
            "seldepth",
            "nodes",
            "time_ms",
            "wall_time_ms",
            "latency_ms",
            "latency_alert",
        }
        move_keys = {"move", "ki2_move", "currentPly", "result_code"}
        has_move = any(payload.get(key) is not None for key in move_keys)
        result_code = payload.get("result_code")
        has_analysis = any(payload.get(key) is not None for key in analysis_keys)

        def _extract_eval(source: Mapping[str, Any]) -> float | None:
            raw = source.get("eval")
            if raw is None:
                raw = source.get("eval_cp")
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                return float(raw)
            return None

        def _extract_analysis(source: Mapping[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            eval_value = _extract_eval(source)
            if eval_value is not None:
                out["eval"] = eval_value
            for key in ("depth", "seldepth", "nodes", "time_ms", "wall_time_ms", "latency_ms"):
                value = source.get(key)
                if is_strict_numeric(value):
                    out[key] = value
            latency_alert = source.get("latency_alert")
            if isinstance(latency_alert, bool):
                out["latency_alert"] = latency_alert
            return out

        topic_moves = f"live.game.{gid}.moves.diff"
        topic_analysis = f"live.game.{gid}.analysis.diff"
        topic_state = f"live.game.{gid}.state.diff"
        assignment_rev = self._state.assignment_rev

        out: list[tuple[str, dict[str, Any]]] = []

        if has_move:
            ply = coerce_int(payload.get("currentPly"))
            move_str = coerce_str(payload.get("move"))
            if (ply and move_str) or result_code is not None:
                move_payload: dict[str, Any] = {
                    "gid": gid,
                    "assignment_rev": assignment_rev,
                }
                if ply is not None:
                    move_payload["ply"] = ply
                if move_str:
                    move_payload["usi"] = move_str
                rc = coerce_int(result_code)
                if rc is not None:
                    move_payload["result_code"] = rc
                ki2_str = coerce_str(payload.get("ki2_move"))
                if move_str and ki2_str:
                    move_payload["ki2_move"] = ki2_str
                sfen_str = coerce_str(payload.get("sfen"))
                if sfen_str:
                    move_payload["sfen"] = sfen_str
                wall_time = payload.get("wall_time_ms")
                if is_strict_numeric(wall_time) and move_str:
                    move_payload["wall_time_ms"] = wall_time
                latency = payload.get("latency_ms")
                if is_strict_numeric(latency) and move_str:
                    move_payload["latency_ms"] = latency
                latency_alert = payload.get("latency_alert")
                if isinstance(latency_alert, bool) and move_str:
                    move_payload["latency_alert"] = latency_alert
                analysis_final = _extract_analysis(payload)
                if analysis_final and (move_str or result_code is not None):
                    move_payload["analysis_final"] = analysis_final
                out.append((topic_moves, move_payload))

        if not has_move and has_analysis:
            ply = coerce_int(payload.get("currentPly"))
            analysis = _extract_analysis(payload)
            if ply and analysis:
                out.append(
                    (topic_analysis, {"gid": gid, "ply": ply, "analysis": analysis, "assignment_rev": assignment_rev})
                )

        # State stream: clock/metadata/result updates (coalescible).
        state_payload: dict[str, Any] = {"gid": gid, "assignment_rev": assignment_rev}
        clock_fields = (
            "side",
            "active",
            "black_remain_ms",
            "white_remain_ms",
            "applied_increment_ms",
            "occurred_at_ms",
            "started_at_ms",
            "pre_black_remain_ms",
            "pre_white_remain_ms",
            "byoyomi_ms_black",
            "byoyomi_ms_white",
            "increment_ms_black",
            "increment_ms_white",
        )
        clock_payload: dict[str, Any] = {}
        clock_raw = payload.get("clock")
        if isinstance(clock_raw, Mapping):
            clock_payload.update(clock_raw)
        for key in clock_fields:
            if payload.get(key) is not None:
                clock_payload[key] = payload.get(key)
        if "time_control_black" in clock_payload:
            clock_payload.pop("time_control_black", None)
        if "time_control_white" in clock_payload:
            clock_payload.pop("time_control_white", None)
        # Timer flicker fix: if 'active' is missing from the clock payload,
        # try to fill it from the cached worker snapshot to prevent frontend
        # from losing track of which clock is running.
        if clock_payload and "active" not in clock_payload:
            worker_snapshot = self._state.worker_snapshots.get(worker_idx)
            if worker_snapshot is not None:
                cached_clock = worker_snapshot.get("clock")
                if isinstance(cached_clock, Mapping):
                    cached_active = cached_clock.get("active")
                    if cached_active is not None:
                        clock_payload["active"] = cached_active
        if clock_payload:
            state_payload["clock"] = clock_payload

        for key in ("end_reason", "meta", "sfen", "black_name", "white_name", "engine_status"):
            if payload.get(key) is not None:
                state_payload[key] = payload.get(key)
        initial_sfen = payload.get("initial_sfen")
        tc_black = payload.get("time_control_black")
        tc_white = payload.get("time_control_white")
        has_initial = bool(isinstance(initial_sfen, str) and initial_sfen.strip())
        has_tc = tc_black is not None and tc_white is not None
        has_tc_keys = "time_control_black" in payload or "time_control_white" in payload
        if has_tc_keys and has_initial != has_tc:
            logger.warning(
                "WS state diff contract violation gid=%s initial_sfen=%s tc_black=%s tc_white=%s",
                gid,
                bool(has_initial),
                tc_black is not None,
                tc_white is not None,
            )
        if has_initial and has_tc:
            state_payload["initial_sfen"] = initial_sfen
            state_payload["time_control_black"] = tc_black
            state_payload["time_control_white"] = tc_white

        if type_str is not None:
            state_payload["type"] = type_str

        # Contract enforcement: clock event payloads must include required fields.
        # If malformed, skip clock emission to avoid corrupting frontend clock state.
        if type_str in {"clock_start", "clock_increment"}:
            required_by_type: dict[str, tuple[str, ...]] = {
                "clock_start": ("active", "black_remain_ms", "white_remain_ms", "started_at_ms"),
                "clock_increment": (
                    "side",
                    "applied_increment_ms",
                    "pre_black_remain_ms",
                    "pre_white_remain_ms",
                    "black_remain_ms",
                    "white_remain_ms",
                    "occurred_at_ms",
                ),
            }
            clock_obj = state_payload.get("clock")
            missing: list[str] = []
            if not isinstance(clock_obj, Mapping):
                missing = list(required_by_type.get(type_str, ()))
            else:
                for key in required_by_type.get(type_str, ()):
                    if clock_obj.get(key) is None:
                        missing.append(key)
            if missing:
                logger.warning(
                    "WS clock contract violation gid=%s type=%s missing=%s",
                    gid,
                    type_str,
                    ",".join(missing),
                )
                state_payload.pop("clock", None)
                # Never emit clock_* type without a valid clock payload.
                state_payload.pop("type", None)

        if "clock" in state_payload:
            only_clock = set(state_payload.keys()) <= {"gid", "assignment_rev", "clock", "type"}
            clock_type = state_payload.get("type")
            if only_clock and clock_type in {None, "clock_start", "clock_increment"}:
                now_ms = time.time() * 1000
                last_sent = self._last_clock_publish_at.get(gid)
                if last_sent is not None and now_ms - last_sent < MIN_CLOCK_PUBLISH_INTERVAL_MS:
                    state_payload = {}
                else:
                    self._last_clock_publish_at[gid] = now_ms

        if len(state_payload) > 2:  # More than just gid and assignment_rev
            out.append((topic_state, state_payload))

        return out
