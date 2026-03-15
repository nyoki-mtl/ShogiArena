"""Engine status diff helpers."""

from __future__ import annotations

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

_ENGINE_STATUS_ROLES = ("black", "white")
_VALID_ENGINE_STATES = {
    "queued",
    "waiting_for_usiok",
    "not_ready",
    "waiting_for_readyok",
    "ready",
    "waiting_for_bestmove",
    "ponder",
    "waiting_for_ponder_bestmove",
    "waiting_for_checkmate",
    "will_quit",
    "quit_completed",
}
_DEFAULT_ENGINE_STATE = "not_ready"


def _normalize_engine_state(state: object) -> str:
    normalized = coerce_str(state)
    if normalized in _VALID_ENGINE_STATES:
        return normalized
    return _DEFAULT_ENGINE_STATE


def compact_engine_status_for_state_diff(raw: object) -> JsonObject | None:
    if not is_str_object_mapping(raw):
        return None
    raw_status = to_json_object(raw)
    compact: JsonObject = {}
    for role in _ENGINE_STATUS_ROLES:
        role_raw = raw_status.get(role)
        role_status = to_json_object(role_raw) if is_str_object_mapping(role_raw) else {}
        updated_at_ms = coerce_int(role_status.get("updated_at_ms"))
        compact[role] = {
            "state": _normalize_engine_state(role_status.get("state")),
            "io_tail": [],
            "updated_at_ms": updated_at_ms if updated_at_ms is not None and updated_at_ms >= 0 else 0,
        }
    return compact


def engine_state_signature(raw: object) -> tuple[str, str] | None:
    if not is_str_object_mapping(raw):
        return None
    status = to_json_object(raw)
    black_raw = status.get("black")
    white_raw = status.get("white")
    black = to_json_object(black_raw) if is_str_object_mapping(black_raw) else {}
    white = to_json_object(white_raw) if is_str_object_mapping(white_raw) else {}
    return (_normalize_engine_state(black.get("state")), _normalize_engine_state(white.get("state")))


def build_engine_status_diff_payload(state_payload: JsonObject) -> JsonObject | None:
    raw_engine_status = state_payload.get("engine_status")
    if not is_str_object_mapping(raw_engine_status):
        return None
    gid = coerce_str(state_payload.get("gid"))
    assignment_rev = coerce_int(state_payload.get("assignment_rev"))
    game_epoch = coerce_int(state_payload.get("game_epoch"))
    if not gid or assignment_rev is None or game_epoch is None:
        return None
    return {
        "gid": gid,
        "assignment_rev": assignment_rev,
        "game_epoch": game_epoch,
        "engine_status": to_json_object(raw_engine_status),
    }


__all__ = [
    "build_engine_status_diff_payload",
    "compact_engine_status_for_state_diff",
    "engine_state_signature",
]
