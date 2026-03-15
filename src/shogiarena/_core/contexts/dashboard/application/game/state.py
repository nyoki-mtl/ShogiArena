"""Game state management for Arena Dashboard.

Handles game state construction, updates, and WebSocket snapshot building.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from shogiarena._core.contexts.dashboard.application.game.cache import GameSnapshotCache
from shogiarena._core.contexts.dashboard.application.game.clock_fields import CLOCK_FIELDS
from shogiarena._core.contexts.dashboard.application.game.state_snapshot_builders import (
    build_ws_snapshot_payload,
    default_game_snapshot,
    ensure_snapshot_list,
    truncate_snapshot_history,
)
from shogiarena._core.contexts.dashboard.ports.progress_converters import WorkerSnapshotNormalizer
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

logger = logging.getLogger(__name__)


@dataclass
class _PendingMoveEntry:
    """Buffered out-of-order move with associated data."""

    ply: int
    move: str | None = None
    ki2_move: str | None = None
    created_at: float = field(default_factory=time.time)


# Constants for pending move buffer management
MAX_PENDING_PLY_GAP = 50
PENDING_MOVE_TTL_SECONDS = 30.0


class GameStateUpdater:
    """Manages game state updates and snapshot construction.

    Handles applying worker updates to game snapshots, building WebSocket
    snapshots, and managing history arrays.

    Args:
        cache: Game snapshot cache instance.
    """

    def __init__(
        self,
        cache: GameSnapshotCache,
        *,
        normalize_snapshot: WorkerSnapshotNormalizer | None = None,
    ) -> None:
        self._cache = cache
        self._pending_buffers: dict[str, dict[int, _PendingMoveEntry]] = {}
        self._normalize_snapshot = normalize_snapshot

    def cache_snapshot(self, gid: str, snapshot: GameSnapshot) -> None:
        """Store a snapshot in the underlying cache."""
        self._cache.set(gid, snapshot)

    def build_ws_snapshot(
        self,
        gid: str,
        snapshot: GameSnapshot,
        *,
        assignment_rev: int = 0,
    ) -> JsonObject | None:
        """Build a WebSocket-formatted game snapshot."""
        return build_ws_snapshot_payload(gid, snapshot, assignment_rev=assignment_rev)

    def build_ws_snapshot_from_cache(self, gid: str, *, assignment_rev: int = 0) -> JsonObject | None:
        """Build a websocket snapshot directly from cached game state."""
        snapshot = self._cache.get(gid)
        if snapshot is None:
            return None
        return self.build_ws_snapshot(gid, snapshot, assignment_rev=assignment_rev)

    def _buffer_move(self, gid: str, ply: int, move: str | None, ki2_move: str | None) -> None:
        """Buffer an out-of-order move for later application.

        Args:
            gid: Game ID.
            ply: The ply number of the move.
            move: The USI move string.
            ki2_move: The KI2 notation move string.
        """
        if gid not in self._pending_buffers:
            self._pending_buffers[gid] = {}
        buffer = self._pending_buffers[gid]

        # Prune expired entries
        now = time.time()
        expired = [p for p, e in buffer.items() if now - e.created_at > PENDING_MOVE_TTL_SECONDS]
        for p in expired:
            del buffer[p]

        buffer[ply] = _PendingMoveEntry(ply=ply, move=move, ki2_move=ki2_move)
        logger.debug(
            "Buffered out-of-order move for gid=%s ply=%s (buffer_size=%s)",
            gid,
            ply,
            len(buffer),
        )

    def _flush_pending_moves(self, gid: str, snap: JsonObject) -> int:
        """Flush buffered moves that can now be applied in order.

        Args:
            gid: Game ID.
            snap: The game snapshot to apply moves to.

        Returns:
            Count of moves flushed.
        """
        buffer = self._pending_buffers.get(gid)
        if not buffer:
            return 0

        # Prune expired entries before flushing
        now = time.time()
        expired = [p for p, e in buffer.items() if now - e.created_at > PENDING_MOVE_TTL_SECONDS]
        for p in expired:
            del buffer[p]
        if not buffer:
            del self._pending_buffers[gid]
            return 0

        moves = ensure_snapshot_list(snap, "moves")
        ki2_moves = ensure_snapshot_list(snap, "ki2_moves")
        flushed = 0

        while True:
            next_ply = len(moves) + 1
            entry = buffer.get(next_ply)
            if entry is None:
                break

            if entry.move:
                moves.append(entry.move)
            if entry.ki2_move and len(ki2_moves) == len(moves) - 1:
                ki2_moves.append(entry.ki2_move)

            del buffer[next_ply]
            flushed += 1

        if flushed > 0:
            logger.debug(
                "Flushed %d pending moves for gid=%s (remaining=%s)",
                flushed,
                gid,
                len(buffer),
            )

        if not buffer:
            del self._pending_buffers[gid]

        return flushed

    def cleanup_game_buffer(self, gid: str) -> None:
        """Clean up pending buffer when game ends.

        Args:
            gid: Game ID to clean up.
        """
        self._pending_buffers.pop(gid, None)

    def update_from_worker(self, gid: str, payload: Mapping[str, JsonValue]) -> None:
        """Apply a worker update to a game snapshot.

        Args:
            gid: Game ID to update.
            payload: Worker update payload.
        """
        existing = self._cache.get(gid)
        snap: JsonObject = to_json_object(existing) if is_str_object_mapping(existing) else default_game_snapshot(gid)

        # Basic metadata.
        if isfen := coerce_str(payload.get("initial_sfen")):
            snap["initial_sfen"] = isfen
        if bname := coerce_str(payload.get("black_name")):
            snap["black_name"] = bname
        if wname := coerce_str(payload.get("white_name")):
            snap["white_name"] = wname
        if sfen := coerce_str(payload.get("sfen")):
            snap["sfen"] = sfen

        if payload.get("time_control_black") is not None:
            snap["time_control_black"] = payload.get("time_control_black")
        if payload.get("time_control_white") is not None:
            snap["time_control_white"] = payload.get("time_control_white")

        clock_payload: JsonObject = {}
        update_type = payload.get("type")
        raw_clock = payload.get("clock")
        if isinstance(raw_clock, Mapping):
            for key, value in raw_clock.items():
                if isinstance(key, str):
                    clock_payload[key] = json_serialize(value)
            if "time_control_black" in clock_payload:
                clock_payload.pop("time_control_black", None)
            if "time_control_white" in clock_payload:
                clock_payload.pop("time_control_white", None)
        for key in CLOCK_FIELDS:
            if payload.get(key) is not None:
                clock_payload[key] = payload.get(key)
        if clock_payload and isinstance(update_type, str):
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
            required = required_by_type.get(update_type)
            if required:
                missing = [key for key in required if clock_payload.get(key) is None]
                if missing:
                    logger.warning(
                        "GameState clock contract violation gid=%s type=%s missing=%s",
                        gid,
                        update_type,
                        ",".join(missing),
                    )
                    clock_payload = {}
        if clock_payload:
            existing_clock = snap.get("clock")
            if is_str_object_mapping(existing_clock):
                merged_clock = to_json_object(existing_clock)
                merged_clock.update(clock_payload)
                snap["clock"] = merged_clock
            else:
                snap["clock"] = clock_payload

        if payload.get("game_result") is not None:
            snap["game_result"] = payload.get("game_result")
            # Game ended: clean up pending buffer
            self.cleanup_game_buffer(gid)
        if payload.get("meta") is not None:
            meta_payload = payload.get("meta")
            if is_str_object_mapping(meta_payload):
                snap["meta"] = to_json_object(meta_payload)
        engine_status = payload.get("engine_status")
        if is_str_object_mapping(engine_status):
            snap["engine_status"] = to_json_object(engine_status)

        # Apply move patch into history arrays.
        ply = coerce_int(payload.get("current_ply"))
        if ply is not None:
            ply = max(0, ply)

        prev_ply = coerce_int(snap.get("current_ply")) or 0
        prev_ply = max(0, prev_ply)

        # Undo/"待った": truncate future history when authoritative ply regresses.
        if ply is not None and ply < prev_ply:
            truncate_snapshot_history(snap, length=ply)
            snap["current_ply"] = ply
            prev_ply = ply
            # Clear buffered moves beyond new ply
            buffer = self._pending_buffers.get(gid)
            if buffer:
                to_remove = [p for p in buffer if p > ply]
                for p in to_remove:
                    del buffer[p]

        moves = ensure_snapshot_list(snap, "moves")
        ki2_moves = ensure_snapshot_list(snap, "ki2_moves")

        move = coerce_str(payload.get("move"))
        if move and ply is not None and ply > 0:
            idx = ply - 1
            if idx < len(moves):
                moves[idx] = move
            elif idx == len(moves):
                moves.append(move)
                # Flush any buffered moves that can now be applied
                flushed = self._flush_pending_moves(gid, snap)
                if flushed > 0:
                    # Update current_ply to reflect all flushed moves.
                    snap["current_ply"] = len(moves)
            else:
                # Buffer out-of-order move instead of skipping (if within gap limit)
                gap = ply - len(moves) - 1
                if gap <= MAX_PENDING_PLY_GAP:
                    ki2_move_raw = payload.get("ki2_move")
                    ki2_move_str = coerce_str(ki2_move_raw)
                    self._buffer_move(gid, ply, move, ki2_move_str)
                else:
                    logger.warning(
                        "Skipping out-of-order move update for gid=%s ply=%s (gap=%s exceeds limit)",
                        gid,
                        ply,
                        gap,
                    )

        ki2_move = coerce_str(payload.get("ki2_move"))
        if ki2_move and ply is not None and ply > 0:
            idx = ply - 1
            if idx < len(ki2_moves):
                ki2_moves[idx] = ki2_move
            elif idx == len(ki2_moves):
                ki2_moves.append(ki2_move)
            else:
                logger.warning(
                    "Skipping out-of-order ki2_move update for gid=%s ply=%s (ki2_moves_len=%s)",
                    gid,
                    ply,
                    len(ki2_moves),
                )

        # Keep current_ply consistent with contiguous moves.
        current_ply = coerce_int(snap.get("current_ply")) or 0
        if ply is not None:
            snap["current_ply"] = min(max(current_ply, ply), len(moves))
        else:
            snap["current_ply"] = min(current_ply, len(moves))

        if self._normalize_snapshot is None:
            return
        normalized_snapshot = self._normalize_snapshot(snap)
        if normalized_snapshot is None:
            logger.warning("Failed to normalize game snapshot for gid=%s", gid)
            return
        self._cache.set(gid, normalized_snapshot)
