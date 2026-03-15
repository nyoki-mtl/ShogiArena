"""Clock diff payload construction and validation."""

from __future__ import annotations

import logging

from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

_logger = logging.getLogger(__name__)


def build_clock_diff_payload(state_payload: JsonObject) -> JsonObject | None:
    raw_clock = state_payload.get("clock")
    if not is_str_object_mapping(raw_clock):
        return None
    gid = coerce_str(state_payload.get("gid"))
    assignment_rev = coerce_int(state_payload.get("assignment_rev"))
    game_epoch = coerce_int(state_payload.get("game_epoch"))
    if not gid or assignment_rev is None or game_epoch is None:
        return None
    out: JsonObject = {
        "gid": gid,
        "assignment_rev": assignment_rev,
        "game_epoch": game_epoch,
        "clock": to_json_object(raw_clock),
    }
    type_str = coerce_str(state_payload.get("type"))
    if type_str in {"clock_start", "clock_increment"}:
        out["type"] = type_str
    return out


def validate_clock_contract(state_payload: JsonObject, *, gid: str, type_str: str) -> None:
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
    if not is_str_object_mapping(clock_obj):
        missing = list(required_by_type.get(type_str, ()))
    else:
        normalized_clock = to_json_object(clock_obj)
        for key in required_by_type.get(type_str, ()):
            if normalized_clock.get(key) is None:
                missing.append(key)
    if missing:
        _logger.warning(
            "WS clock contract violation gid=%s type=%s missing=%s",
            gid,
            type_str,
            ",".join(missing),
        )
        state_payload.pop("clock", None)
        state_payload.pop("type", None)


__all__ = ["build_clock_diff_payload", "validate_clock_contract"]
