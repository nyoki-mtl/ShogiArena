"""Snapshot builders and history helpers for dashboard game state."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

logger = logging.getLogger(__name__)


def _to_json_list(values: Sequence[object]) -> list[JsonValue]:
    """Normalize sequence values to JsonValue."""
    return [json_serialize(value) for value in values]


def default_game_snapshot(gid: str) -> JsonObject:
    """Create a default empty game snapshot."""
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
        "engine_wall_times_ms": [],
        "latency_deltas_ms": [],
        "latency_alerts": [],
        "current_ply": 0,
        "sfen": "startpos",
    }


def _derive_active_from_sfen(snapshot: Mapping[str, object], current_ply: int) -> str:
    """Derive active side from sfen and ply count."""
    current_sfen_raw = snapshot.get("sfen")
    current_sfen = current_sfen_raw.strip() if isinstance(current_sfen_raw, str) else None
    if current_sfen and current_sfen != "startpos":
        parts = current_sfen.split()
        if len(parts) >= 2 and parts[1] in ("b", "w"):
            return "black" if parts[1] == "b" else "white"

    initial_sfen_raw = snapshot.get("initial_sfen")
    initial_sfen = initial_sfen_raw.strip() if isinstance(initial_sfen_raw, str) else "startpos"
    if not initial_sfen:
        initial_sfen = "startpos"

    if initial_sfen == "startpos":
        initial_turn = "b"
    else:
        parts = initial_sfen.split()
        if len(parts) >= 2 and parts[1] in ("b", "w"):
            initial_turn = parts[1]
        else:
            logger.warning("Invalid SFEN format, cannot determine side to move: %s", initial_sfen)
            return "black"

    if initial_turn == "b":
        return "black" if current_ply % 2 == 0 else "white"
    return "white" if current_ply % 2 == 0 else "black"


def build_ws_snapshot_payload(
    gid: str,
    snapshot: GameSnapshot,
    *,
    assignment_rev: int = 0,
) -> JsonObject | None:
    """Build a WebSocket-formatted game snapshot payload."""
    initial_sfen = snapshot["initial_sfen"]
    tc_black = snapshot.get("time_control_black")
    tc_white = snapshot.get("time_control_white")
    has_initial = bool(initial_sfen.strip())
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
    moves_raw = snapshot["moves"]
    ki2_moves = snapshot["ki2_moves"]
    eval_black = snapshot["eval_black"]
    eval_white = snapshot["eval_white"]
    depth_values = snapshot["depth_values"]
    seldepth_values = snapshot["seldepth_values"]
    nodes_values = snapshot["nodes_values"]
    move_times = snapshot["move_times_ms"]
    wall_times = snapshot["wall_times_ms"]
    engine_wall_times = snapshot.get("engine_wall_times_ms", [])
    latency_deltas = snapshot["latency_deltas_ms"]
    latency_alerts = snapshot["latency_alerts"]

    moves: list[JsonObject] = []
    for idx, move in enumerate(moves_raw):
        if not move.strip():
            break
        entry: JsonObject = {"ply": idx + 1, "usi": move}
        if idx < len(ki2_moves) and ki2_moves[idx].strip():
            entry["ki2_move"] = ki2_moves[idx]
        analysis_final: JsonObject = {}
        # A per-ply hole (a move nobody measured) stays out of analysis rather than
        # being reported as a value.
        if idx < len(eval_black) and eval_black[idx] is not None:
            analysis_final["eval"] = float(eval_black[idx] or 0)
        elif idx < len(eval_white) and eval_white[idx] is not None:
            analysis_final["eval"] = -float(eval_white[idx] or 0)
        for key, series in (
            ("depth", depth_values),
            ("seldepth", seldepth_values),
            ("nodes", nodes_values),
            ("time_ms", move_times),
        ):
            if idx < len(series) and series[idx] is not None:
                analysis_final[key] = series[idx]
        if analysis_final:
            entry["analysis_final"] = analysis_final
        moves.append(entry)

    state: JsonObject = {}
    clock_dict: JsonObject = {}
    clock_raw = snapshot.get("clock")
    if is_str_object_mapping(clock_raw):
        clock_dict.update(to_json_object(clock_raw))
    for source, target in (
        ("clock_active", "active"),
        ("black_remain_ms", "black_remain_ms"),
        ("white_remain_ms", "white_remain_ms"),
        ("clock_started_at_ms", "started_at_ms"),
        ("clock_occurred_at_ms", "occurred_at_ms"),
    ):
        value = snapshot.get(source)
        if value is not None:
            clock_dict[target] = json_serialize(value)
    if clock_dict:
        if "active" not in clock_dict:
            clock_dict["active"] = _derive_active_from_sfen(
                snapshot,
                len(moves),
            )
        state["clock"] = clock_dict
    game_result = snapshot.get("game_result")
    if game_result is not None:
        state["game_result"] = game_result
    meta = snapshot.get("meta")
    if meta is not None:
        state["meta"] = json_serialize(meta)

    ws_snapshot: JsonObject = {
        "gid": gid,
        "initial_sfen": initial_sfen,
        "moves": _to_json_list(moves),
        "ki2_moves": _to_json_list(ki2_moves),
        "eval_black": _to_json_list(eval_black),
        "eval_white": _to_json_list(eval_white),
        "nodes_values": _to_json_list(nodes_values),
        "depth_values": _to_json_list(depth_values),
        "seldepth_values": _to_json_list(seldepth_values),
        "move_times_ms": _to_json_list(move_times),
        "wall_times_ms": _to_json_list(wall_times),
        "engine_wall_times_ms": _to_json_list(engine_wall_times),
        "latency_deltas_ms": _to_json_list(latency_deltas),
        "latency_alerts": _to_json_list(latency_alerts),
        "current_ply": len(moves),
        "assignment_rev": assignment_rev,
        "game_epoch": max(0, assignment_rev),
    }
    if state:
        ws_snapshot["state"] = state
    ws_snapshot["sfen"] = snapshot["sfen"]
    black_name = snapshot.get("black_name")
    if black_name is not None:
        ws_snapshot["black_name"] = black_name
    white_name = snapshot.get("white_name")
    if white_name is not None:
        ws_snapshot["white_name"] = white_name
    time_control_black = snapshot.get("time_control_black")
    if time_control_black is not None:
        ws_snapshot["time_control_black"] = json_serialize(time_control_black)
    time_control_white = snapshot.get("time_control_white")
    if time_control_white is not None:
        ws_snapshot["time_control_white"] = json_serialize(time_control_white)
    engine_status = snapshot.get("engine_status")
    if engine_status is not None:
        ws_snapshot["engine_status"] = json_serialize(engine_status)
    return ws_snapshot


def ensure_snapshot_list(snapshot: JsonObject, key: str) -> list[JsonValue]:
    """Ensure a key in the snapshot is a normalized JsonValue list."""
    value = snapshot.get(key)
    if isinstance(value, list):
        normalized = _to_json_list(value)
        snapshot[key] = normalized
        return normalized
    created: list[JsonValue] = []
    snapshot[key] = created
    return created


def truncate_snapshot_history(snapshot: JsonObject, *, length: int) -> None:
    """Truncate history arrays to specified length."""
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
        "engine_wall_times_ms",
        "latency_deltas_ms",
        "latency_alerts",
    )
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, list) and len(value) > length:
            del value[length:]
