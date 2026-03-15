"""Meta diff payload construction."""

from __future__ import annotations

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

_META_DIFF_FIELDS = (
    "game_result",
    "meta",
    "sfen",
    "initial_sfen",
    "time_control_black",
    "time_control_white",
)


def build_meta_diff_payload(state_payload: JsonObject) -> JsonObject | None:
    gid = coerce_str(state_payload.get("gid"))
    assignment_rev = coerce_int(state_payload.get("assignment_rev"))
    game_epoch = coerce_int(state_payload.get("game_epoch"))
    if not gid or assignment_rev is None or game_epoch is None:
        return None
    out: JsonObject = {"gid": gid, "assignment_rev": assignment_rev, "game_epoch": game_epoch}
    has_meta = False
    for field in _META_DIFF_FIELDS:
        value = state_payload.get(field)
        if value is not None:
            has_meta = True
            out[field] = value
    return out if has_meta else None


__all__ = ["build_meta_diff_payload"]
