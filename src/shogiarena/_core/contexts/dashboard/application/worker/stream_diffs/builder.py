"""Main entrypoint for building game diff envelopes from worker payloads."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from shogiarena._core.contexts.dashboard.application.stream_topics import (
    topic_live_game_analysis_diff,
    topic_live_game_clock_diff,
    topic_live_game_engine_status_diff,
    topic_live_game_meta_diff,
    topic_live_game_moves_diff,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

from .clock_diffs import build_clock_diff_payload, validate_clock_contract
from .engine_status_diffs import build_engine_status_diff_payload, engine_state_signature
from .meta_diffs import build_meta_diff_payload
from .move_diffs import build_move_payload, extract_analysis, next_move_seq
from .state_diffs import build_state_payload

ExtractGidFn = Callable[[Mapping[str, object]], str | None]
GetWorkerSnapshotFn = Callable[[int], GameSnapshot | None]


def build_game_diff_envelopes(
    *,
    worker_idx: int,
    payload: Mapping[str, object],
    extract_gid: ExtractGidFn,
    assignment_rev: int,
    next_move_seq_by_gid: dict[str, int],
    get_worker_snapshot: GetWorkerSnapshotFn,
    min_clock_publish_interval_ms: int,
    last_clock_publish_at: dict[str, float],
    last_engine_state_signature: dict[str, tuple[str, str]],
) -> list[tuple[str, JsonObject]]:
    """Build websocket diff envelopes from one worker payload."""
    payload_json = to_json_object(payload)
    gid = extract_gid(payload_json)
    if not gid:
        return []
    game_epoch = max(0, assignment_rev)

    type_value = payload_json.get("type")
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
        "is_latency_alert",
    }
    move_keys = {"move", "ki2_move", "current_ply", "game_result"}
    has_move = any(payload_json.get(key) is not None for key in move_keys)
    game_result = payload_json.get("game_result")
    has_analysis = any(payload_json.get(key) is not None for key in analysis_keys)

    topic_moves = topic_live_game_moves_diff(gid)
    topic_analysis = topic_live_game_analysis_diff(gid)
    topic_meta = topic_live_game_meta_diff(gid)
    topic_clock = topic_live_game_clock_diff(gid)
    topic_engine_status = topic_live_game_engine_status_diff(gid)

    out: list[tuple[str, JsonObject]] = []

    if has_move:
        move_payload = build_move_payload(
            payload_json,
            gid=gid,
            assignment_rev=assignment_rev,
            game_epoch=game_epoch,
            game_result=game_result,
        )
        if move_payload is not None:
            move_payload["move_seq"] = next_move_seq(gid, next_move_seq_by_gid)
            out.append((topic_moves, move_payload))

    if not has_move and has_analysis:
        ply = coerce_int(payload_json.get("current_ply"))
        analysis = extract_analysis(payload_json)
        if ply and analysis:
            out.append(
                (
                    topic_analysis,
                    {
                        "gid": gid,
                        "ply": ply,
                        "analysis": analysis,
                        "assignment_rev": assignment_rev,
                        "game_epoch": game_epoch,
                    },
                )
            )

    state_payload = build_state_payload(
        payload_json,
        gid=gid,
        assignment_rev=assignment_rev,
        game_epoch=game_epoch,
        worker_idx=worker_idx,
        get_worker_snapshot=get_worker_snapshot,
        type_str=type_str,
    )
    if type_str is not None:
        state_payload["type"] = type_str

    if type_str in {"engine_io", "handshake_log"}:
        sig = engine_state_signature(state_payload.get("engine_status"))
        if sig is None:
            state_payload.pop("type", None)
        else:
            previous_signature = last_engine_state_signature.get(gid)
            if previous_signature == sig:
                state_payload.pop("engine_status", None)
                state_payload.pop("type", None)
            else:
                last_engine_state_signature[gid] = sig

    if type_str in {"clock_start", "clock_increment"}:
        validate_clock_contract(state_payload, gid=gid, type_str=type_str)

    if "clock" in state_payload:
        only_clock = set(state_payload.keys()) <= {"gid", "assignment_rev", "game_epoch", "clock", "type"}
        clock_type = state_payload.get("type")
        if only_clock and clock_type in {None, "clock_start", "clock_increment"}:
            now_ms = time.time() * 1000
            last_sent = last_clock_publish_at.get(gid)
            if last_sent is not None and now_ms - last_sent < min_clock_publish_interval_ms:
                state_payload = {}
            else:
                last_clock_publish_at[gid] = now_ms

    clock_diff = build_clock_diff_payload(state_payload)
    if clock_diff is not None:
        out.append((topic_clock, clock_diff))

    meta_diff = build_meta_diff_payload(state_payload)
    if meta_diff is not None:
        out.append((topic_meta, meta_diff))

    engine_status_diff = build_engine_status_diff_payload(state_payload)
    if engine_status_diff is not None:
        out.append((topic_engine_status, engine_status_diff))

    return out


__all__ = ["build_game_diff_envelopes"]
