"""State payload construction from worker updates."""

from __future__ import annotations

import logging
from collections.abc import Callable

from shogiarena._core.contexts.dashboard.application.game.clock_fields import CLOCK_FIELDS
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.snapshots import GameSnapshot

from .engine_status_diffs import compact_engine_status_for_state_diff

_logger = logging.getLogger(__name__)

GetWorkerSnapshotFn = Callable[[int], GameSnapshot | None]


def build_state_payload(
    payload: JsonObject,
    *,
    gid: str,
    assignment_rev: int,
    game_epoch: int,
    worker_idx: int,
    get_worker_snapshot: GetWorkerSnapshotFn,
    type_str: str | None,
) -> JsonObject:
    state_payload: JsonObject = {"gid": gid, "assignment_rev": assignment_rev, "game_epoch": game_epoch}
    clock_payload: JsonObject = {}
    clock_raw = payload.get("clock")
    if is_str_object_mapping(clock_raw):
        for key, value in clock_raw.items():
            clock_payload[key] = json_serialize(value)
    for key in CLOCK_FIELDS:
        value = payload.get(key)
        if value is not None:
            clock_payload[key] = value
    if "time_control_black" in clock_payload:
        clock_payload.pop("time_control_black", None)
    if "time_control_white" in clock_payload:
        clock_payload.pop("time_control_white", None)
    if clock_payload and "active" not in clock_payload:
        worker_snapshot = get_worker_snapshot(worker_idx)
        if worker_snapshot is not None:
            cached_clock = worker_snapshot.get("clock")
            if is_str_object_mapping(cached_clock):
                cached_active = cached_clock.get("active")
                if cached_active is not None:
                    clock_payload["active"] = json_serialize(cached_active)
    if clock_payload:
        state_payload["clock"] = clock_payload

    for key in ("game_result", "meta", "sfen", "engine_status"):
        value = payload.get(key)
        if value is None:
            continue
        if key in {"game_result", "sfen"} and type_str == "move_progress":
            continue
        if key == "engine_status":
            if type_str == "move_progress":
                continue
            compact_status = compact_engine_status_for_state_diff(value)
            if compact_status is not None:
                state_payload["engine_status"] = compact_status
            continue
        state_payload[key] = value
    initial_sfen = payload.get("initial_sfen")
    tc_black = payload.get("time_control_black")
    tc_white = payload.get("time_control_white")
    has_initial = bool(isinstance(initial_sfen, str) and initial_sfen.strip())
    has_tc = tc_black is not None and tc_white is not None
    has_tc_keys = "time_control_black" in payload or "time_control_white" in payload
    if has_tc_keys and has_initial != has_tc:
        _logger.warning(
            "WS state diff contract violation gid=%s initial_sfen=%s tc_black=%s tc_white=%s",
            gid,
            bool(has_initial),
            tc_black is not None,
            tc_white is not None,
        )
    if has_initial and has_tc:
        state_payload["initial_sfen"] = json_serialize(initial_sfen)
        state_payload["time_control_black"] = json_serialize(tc_black)
        state_payload["time_control_white"] = json_serialize(tc_white)

    return state_payload


__all__ = ["GetWorkerSnapshotFn", "build_state_payload"]
