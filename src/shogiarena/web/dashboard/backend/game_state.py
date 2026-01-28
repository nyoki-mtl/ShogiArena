"""Game state management for Arena Dashboard.

Handles game state construction, updates, and WebSocket snapshot building.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shogiarena.web.dashboard.backend.game_cache import GameSnapshotCache

logger = logging.getLogger(__name__)


@dataclass
class PendingMoveEntry:
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

    def __init__(self, cache: GameSnapshotCache) -> None:
        self._cache = cache
        self._pending_buffers: dict[str, dict[int, PendingMoveEntry]] = {}

    @staticmethod
    def default_snapshot(gid: str) -> dict[str, Any]:
        """Create a default empty game snapshot.

        Args:
            gid: Game ID for the snapshot.

        Returns:
            A new game snapshot with default values.
        """
        return {
            "game_id": gid,
            "initial_sfen": "startpos",
            "black_name": None,
            "white_name": None,
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
            "currentPly": 0,
            "sfen": "startpos",
        }

    @staticmethod
    def _derive_active_from_sfen(snapshot: Mapping[str, Any], current_ply: int) -> str:
        """Derive active side from sfen and ply count.

        Handles handicap games where initial turn may be white.
        SFEN format: "position b/w ..." where b=black, w=white.
        "startpos" always starts with black.

        Args:
            snapshot: Game snapshot containing sfen or initial_sfen.
            current_ply: Current ply count (0-indexed move count).

        Returns:
            "black" or "white" indicating whose turn it is.
        """
        # Check if we have a current position sfen (not initial).
        # If sfen represents the current position, use its side_to_move directly
        # without parity correction.
        current_sfen = snapshot.get("sfen")
        if isinstance(current_sfen, str) and current_sfen.strip() and current_sfen != "startpos":
            parts = current_sfen.split()
            if len(parts) >= 2 and parts[1] in ("b", "w"):
                # Current sfen directly tells us whose turn it is.
                return "black" if parts[1] == "b" else "white"

        # Fall back to initial_sfen with parity correction.
        initial_sfen = snapshot.get("initial_sfen")
        if not isinstance(initial_sfen, str) or not initial_sfen.strip():
            initial_sfen = "startpos"

        # "startpos" means standard starting position (black to move).
        if initial_sfen == "startpos":
            initial_turn = "b"
        else:
            # SFEN format: "board side_to_move hand_pieces move_count"
            # Example: "lnsgkgsnl/... b - 1" (black to move)
            parts = initial_sfen.split()
            if len(parts) >= 2 and parts[1] in ("b", "w"):
                initial_turn = parts[1]
            else:
                # Invalid SFEN format: fall back to black.
                logger.warning("Invalid SFEN format, cannot determine side to move: %s", initial_sfen)
                return "black"

        # Determine current turn: initial turn + ply parity.
        # If initial is black and ply is even -> black's turn.
        # If initial is black and ply is odd -> white's turn.
        # If initial is white and ply is even -> white's turn.
        # If initial is white and ply is odd -> black's turn.
        if initial_turn == "b":
            return "black" if current_ply % 2 == 0 else "white"
        return "white" if current_ply % 2 == 0 else "black"

    def build_ws_snapshot(
        self,
        gid: str,
        snapshot: Mapping[str, Any],
        *,
        assignment_rev: int = 0,
    ) -> dict[str, Any] | None:
        """Build a WebSocket-formatted game snapshot.

        Transforms internal snapshot format to the format expected by the frontend.

        Args:
            gid: Game ID.
            snapshot: Internal game snapshot.
            assignment_rev: Current assignment revision.

        Returns:
            WebSocket-formatted snapshot or None if input is invalid.
        """
        if not isinstance(snapshot, Mapping):
            return None
        initial_sfen_raw = snapshot.get("initial_sfen")
        initial_sfen = initial_sfen_raw if isinstance(initial_sfen_raw, str) else None
        tc_black = snapshot.get("time_control_black")
        tc_white = snapshot.get("time_control_white")
        has_initial = bool(isinstance(initial_sfen, str) and initial_sfen.strip() != "")
        has_tc = tc_black is not None and tc_white is not None
        if not (has_initial and has_tc):
            logger.warning(
                "WS snapshot contract violation gid=%s initial_sfen=%s tc_black=%s tc_white=%s",
                gid,
                bool(has_initial),
                tc_black is not None,
                tc_white is not None,
            )
            return None
        moves_raw = snapshot.get("moves")
        if not isinstance(moves_raw, list):
            moves_raw = []
        ki2_moves = snapshot.get("ki2_moves")
        if not isinstance(ki2_moves, list):
            ki2_moves = []
        eval_black = snapshot.get("eval_black")
        if not isinstance(eval_black, list):
            eval_black = []
        eval_white = snapshot.get("eval_white")
        if not isinstance(eval_white, list):
            eval_white = []
        depth_values = snapshot.get("depth_values")
        if not isinstance(depth_values, list):
            depth_values = []
        seldepth_values = snapshot.get("seldepth_values")
        if not isinstance(seldepth_values, list):
            seldepth_values = []
        nodes_values = snapshot.get("nodes_values")
        if not isinstance(nodes_values, list):
            nodes_values = []
        move_times = snapshot.get("move_times_ms")
        if not isinstance(move_times, list):
            move_times = []
        wall_times = snapshot.get("wall_times_ms")
        if not isinstance(wall_times, list):
            wall_times = []
        latency_deltas = snapshot.get("latency_deltas_ms")
        if not isinstance(latency_deltas, list):
            latency_deltas = []
        latency_alerts = snapshot.get("latency_alerts")
        if not isinstance(latency_alerts, list):
            latency_alerts = []

        moves: list[dict[str, Any]] = []
        for idx, move in enumerate(moves_raw):
            if not isinstance(move, str) or not move.strip():
                break
            entry: dict[str, Any] = {"ply": idx + 1, "usi": move}
            if idx < len(ki2_moves) and isinstance(ki2_moves[idx], str) and ki2_moves[idx].strip():
                entry["ki2_move"] = ki2_moves[idx]
            analysis_final: dict[str, Any] = {}
            if idx < len(eval_black) and isinstance(eval_black[idx], int | float):
                analysis_final["eval"] = float(eval_black[idx])
            elif idx < len(eval_white) and isinstance(eval_white[idx], int | float):
                analysis_final["eval"] = -float(eval_white[idx])
            if idx < len(depth_values) and isinstance(depth_values[idx], int | float):
                analysis_final["depth"] = depth_values[idx]
            if idx < len(seldepth_values) and isinstance(seldepth_values[idx], int | float):
                analysis_final["seldepth"] = seldepth_values[idx]
            if idx < len(nodes_values) and isinstance(nodes_values[idx], int | float):
                analysis_final["nodes"] = nodes_values[idx]
            if idx < len(move_times) and isinstance(move_times[idx], int | float):
                analysis_final["time_ms"] = move_times[idx]
            if analysis_final:
                entry["analysis_final"] = analysis_final
            moves.append(entry)

        state: dict[str, Any] = {}
        clock_raw = snapshot.get("clock")
        if isinstance(clock_raw, Mapping):
            clock_dict = dict(clock_raw)
            # Timer flicker fix: ensure 'active' is always present in clock payload.
            # If 'active' is missing, derive from sfen to handle handicap games correctly.
            if "active" not in clock_dict:
                clock_dict["active"] = self._derive_active_from_sfen(snapshot, len(moves))
            state["clock"] = clock_dict
        for key in ("result_code", "end_reason", "meta"):
            if snapshot.get(key) is not None:
                state[key] = snapshot.get(key)

        ws_snapshot: dict[str, Any] = {
            "gid": gid,
            "initial_sfen": initial_sfen,
            "moves": moves,
            "ki2_moves": ki2_moves,
            "eval_black": eval_black,
            "eval_white": eval_white,
            "nodes_values": nodes_values,
            "depth_values": depth_values,
            "seldepth_values": seldepth_values,
            "move_times_ms": move_times,
            "wall_times_ms": wall_times,
            "latency_deltas_ms": latency_deltas,
            "latency_alerts": latency_alerts,
            "currentPly": len(moves),
            "assignment_rev": assignment_rev,
        }
        if state:
            ws_snapshot["state"] = state
        if isinstance(snapshot.get("sfen"), str):
            ws_snapshot["sfen"] = snapshot.get("sfen")
        if snapshot.get("black_name") is not None:
            ws_snapshot["black_name"] = snapshot.get("black_name")
        if snapshot.get("white_name") is not None:
            ws_snapshot["white_name"] = snapshot.get("white_name")
        if snapshot.get("time_control_black") is not None:
            ws_snapshot["time_control_black"] = snapshot.get("time_control_black")
        if snapshot.get("time_control_white") is not None:
            ws_snapshot["time_control_white"] = snapshot.get("time_control_white")
        return ws_snapshot

    @staticmethod
    def ensure_list(snapshot: dict[str, Any], key: str) -> list[Any]:
        """Ensure a key in the snapshot is a list, creating if needed.

        Args:
            snapshot: Game snapshot dict.
            key: Key to ensure is a list.

        Returns:
            The list value at the key.
        """
        value = snapshot.get(key)
        if isinstance(value, list):
            return value
        created: list[Any] = []
        snapshot[key] = created
        return created

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

        buffer[ply] = PendingMoveEntry(ply=ply, move=move, ki2_move=ki2_move)
        logger.debug(
            "Buffered out-of-order move for gid=%s ply=%s (buffer_size=%s)",
            gid,
            ply,
            len(buffer),
        )

    def _flush_pending_moves(self, gid: str, snap: dict[str, Any]) -> int:
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

        moves = self.ensure_list(snap, "moves")
        ki2_moves = self.ensure_list(snap, "ki2_moves")
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

    @staticmethod
    def truncate_history(snapshot: dict[str, Any], *, length: int) -> None:
        """Truncate history arrays to specified length.

        Used for undo/"待った" operations to remove future history.
        Keep this in sync with frontend HISTORY_ARRAY_KEYS.

        Args:
            snapshot: Game snapshot to truncate.
            length: Target length for arrays.
        """
        keys = (
            "moves",
            "ki2_moves",
            "eval_black",
            "eval_white",
            "nodes_values",
            "depth_values",
            "seldepth_values",
            "move_times_ms",
            "wall_times_ms",
            "latency_deltas_ms",
            "latency_alerts",
        )
        for key in keys:
            value = snapshot.get(key)
            if isinstance(value, list) and len(value) > length:
                del value[length:]

    def update_from_worker(self, gid: str, payload: Mapping[str, Any]) -> None:
        """Apply a worker update to a game snapshot.

        Args:
            gid: Game ID to update.
            payload: Worker update payload.
        """
        existing = self._cache.get(gid)
        snap = dict(existing) if isinstance(existing, dict) else self.default_snapshot(gid)

        # Basic metadata.
        initial_sfen = payload.get("initial_sfen")
        if isinstance(initial_sfen, str) and initial_sfen.strip():
            snap["initial_sfen"] = initial_sfen
        black_name = payload.get("black_name")
        if isinstance(black_name, str):
            snap["black_name"] = black_name
        white_name = payload.get("white_name")
        if isinstance(white_name, str):
            snap["white_name"] = white_name
        sfen = payload.get("sfen")
        if isinstance(sfen, str) and sfen.strip():
            snap["sfen"] = sfen

        if payload.get("time_control_black") is not None:
            snap["time_control_black"] = payload.get("time_control_black")
        if payload.get("time_control_white") is not None:
            snap["time_control_white"] = payload.get("time_control_white")

        clock_fields = (
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
        raw_clock = payload.get("clock")
        if isinstance(raw_clock, Mapping):
            clock_payload.update(raw_clock)
            if "time_control_black" in clock_payload:
                clock_payload.pop("time_control_black", None)
            if "time_control_white" in clock_payload:
                clock_payload.pop("time_control_white", None)
        for key in clock_fields:
            if payload.get(key) is not None:
                clock_payload[key] = payload.get(key)
        if clock_payload:
            existing_clock = snap.get("clock")
            if isinstance(existing_clock, Mapping):
                merged_clock = dict(existing_clock)
                merged_clock.update(clock_payload)
                snap["clock"] = merged_clock
            else:
                snap["clock"] = clock_payload

        if payload.get("result_code") is not None:
            snap["result_code"] = payload.get("result_code")
            # Game ended: clean up pending buffer
            self.cleanup_game_buffer(gid)
        if payload.get("end_reason") is not None:
            snap["end_reason"] = payload.get("end_reason")
            # Game ended: clean up pending buffer (idempotent if already called)
            self.cleanup_game_buffer(gid)
        if payload.get("meta") is not None:
            snap["meta"] = payload.get("meta")

        # Apply move patch into history arrays.
        current_ply_raw = payload.get("currentPly")
        ply = int(current_ply_raw) if isinstance(current_ply_raw, int | float) else None
        if ply is not None:
            ply = max(0, ply)

        prev_ply_raw = snap.get("currentPly")
        prev_ply = int(prev_ply_raw) if isinstance(prev_ply_raw, int | float) else 0
        prev_ply = max(0, prev_ply)

        def _trim_at_first_missing(arr: list[Any]) -> None:
            for i, item in enumerate(arr):
                if not isinstance(item, str) or not item.strip():
                    if i < len(arr):
                        del arr[i:]
                    return

        # Undo/"待った": truncate future history when authoritative ply regresses.
        if ply is not None and ply < prev_ply:
            self.truncate_history(snap, length=ply)
            snap["currentPly"] = ply
            prev_ply = ply
            # Clear buffered moves beyond new ply
            buffer = self._pending_buffers.get(gid)
            if buffer:
                to_remove = [p for p in buffer if p > ply]
                for p in to_remove:
                    del buffer[p]

        moves = self.ensure_list(snap, "moves")
        ki2_moves = self.ensure_list(snap, "ki2_moves")

        move = payload.get("move")
        if isinstance(move, str) and move.strip() and ply is not None and ply > 0:
            idx = ply - 1
            if idx < len(moves):
                moves[idx] = move
            elif idx == len(moves):
                moves.append(move)
                # Flush any buffered moves that can now be applied
                flushed = self._flush_pending_moves(gid, snap)
                if flushed > 0:
                    # Update currentPly to reflect all flushed moves
                    snap["currentPly"] = len(moves)
            else:
                # Buffer out-of-order move instead of skipping (if within gap limit)
                gap = ply - len(moves) - 1
                if gap <= MAX_PENDING_PLY_GAP:
                    ki2_move_raw = payload.get("ki2_move")
                    ki2_move_str = ki2_move_raw if isinstance(ki2_move_raw, str) else None
                    self._buffer_move(gid, ply, move, ki2_move_str)
                else:
                    logger.warning(
                        "Skipping out-of-order move update for gid=%s ply=%s (gap=%s exceeds limit)",
                        gid,
                        ply,
                        gap,
                    )

        ki2_move = payload.get("ki2_move")
        if isinstance(ki2_move, str) and ki2_move.strip() and ply is not None and ply > 0:
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

        # Sanitize legacy holey arrays (older versions padded None).
        _trim_at_first_missing(moves)
        _trim_at_first_missing(ki2_moves)

        # Keep currentPly consistent with contiguous moves.
        if ply is not None:
            snap["currentPly"] = min(max(int(snap.get("currentPly") or 0), ply), len(moves))
        else:
            snap["currentPly"] = min(int(snap.get("currentPly") or 0), len(moves))

        self._cache.set(gid, snap)
