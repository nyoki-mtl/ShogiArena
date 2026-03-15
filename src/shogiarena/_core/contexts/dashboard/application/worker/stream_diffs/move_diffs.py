"""Move and analysis diff helpers."""

from __future__ import annotations

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str, is_strict_numeric


def next_move_seq(gid: str, next_move_seq_by_gid: dict[str, int]) -> int:
    next_seq = next_move_seq_by_gid.get(gid, 0) + 1
    next_move_seq_by_gid[gid] = next_seq
    return next_seq


def _extract_eval(source: JsonObject) -> float | None:
    raw = source.get("eval")
    if raw is None:
        raw = source.get("eval_cp")
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    return None


def extract_analysis(source: JsonObject) -> JsonObject:
    out: JsonObject = {}
    eval_value = _extract_eval(source)
    if eval_value is not None:
        out["eval"] = eval_value
    for key in ("depth", "seldepth", "nodes", "time_ms", "wall_time_ms", "latency_ms"):
        value = source.get(key)
        if is_strict_numeric(value):
            out[key] = value
    latency_alert = source.get("is_latency_alert")
    if isinstance(latency_alert, bool):
        out["is_latency_alert"] = latency_alert
    return out


def build_move_payload(
    payload: JsonObject,
    *,
    gid: str,
    assignment_rev: int,
    game_epoch: int,
    game_result: object,
) -> JsonObject | None:
    ply = coerce_int(payload.get("current_ply"))
    move_str = coerce_str(payload.get("move"))
    if not ((ply and move_str) or game_result is not None):
        return None

    move_payload: JsonObject = {
        "gid": gid,
        "assignment_rev": assignment_rev,
        "game_epoch": game_epoch,
    }
    if ply is not None:
        move_payload["ply"] = ply
    if move_str:
        move_payload["usi"] = move_str
    result_name = coerce_str(game_result)
    if result_name is not None:
        move_payload["game_result"] = result_name
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
    latency_alert = payload.get("is_latency_alert")
    if isinstance(latency_alert, bool) and move_str:
        move_payload["is_latency_alert"] = latency_alert
    analysis_final = extract_analysis(payload)
    if analysis_final and (move_str or result_name is not None):
        move_payload["analysis_final"] = analysis_final
    return move_payload


__all__ = ["build_move_payload", "extract_analysis", "next_move_seq"]
