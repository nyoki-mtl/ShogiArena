"""DTO and diff payload builders for worker snapshot events."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.game_session.application.progress.events import (
    ClockIncrementEvent,
    ClockStartEvent,
    EngineIoEvent,
    GameAssignedEvent,
    HandshakeLogEvent,
    MoveProgressEvent,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    WorkerSnapshotModel,
    to_worker_snapshot_model,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import to_json_value
from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.live_stream_payloads import (
    ClockIncrementDiffPayload,
    ClockStartDiffPayload,
    EngineLogDiffPayload,
    GameAssignedDiffPayload,
    MoveProgressDiffPayload,
)
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.snapshots import EngineIoTailEntry, EngineStatusEntry, EngineStatusMap, GameSnapshot


def _engine_status_dto(model: WorkerSnapshotModel) -> EngineStatusMap:
    engine_status: EngineStatusMap = {}
    for role, entry in model.engine_status.items():
        tails: list[EngineIoTailEntry] = []
        for tail in entry.io_tail:
            tail_entry: EngineIoTailEntry = {"dir": tail.dir, "line": tail.line, "ts": tail.ts}
            if tail.state is not None:
                tail_entry["state"] = tail.state
            tails.append(tail_entry)
        entry_payload: EngineStatusEntry = {
            "state": entry.state,
            "io_tail": tails,
            "updated_at_ms": entry.updated_at_ms,
        }
        engine_status[role] = entry_payload
    return engine_status


def _engine_status_compact_dto(model: WorkerSnapshotModel) -> EngineStatusMap:
    compact: EngineStatusMap = {}
    for role, entry in model.engine_status.items():
        entry_payload: EngineStatusEntry = {
            "state": entry.state,
            "io_tail": [],
            "updated_at_ms": entry.updated_at_ms,
        }
        compact[role] = entry_payload
    return compact


def to_worker_snapshot_dto(model: WorkerSnapshotModel) -> GameSnapshot:
    dto: GameSnapshot = {
        "game_id": model.game_id,
        "initial_sfen": model.initial_sfen,
        "black_name": model.black_name,
        "white_name": model.white_name,
        "moves": list(model.moves),
        "ki2_moves": list(model.ki2_moves),
        "eval_black": list(model.eval_black),
        "eval_white": list(model.eval_white),
        "nodes_values": list(model.nodes_values),
        "depth_values": list(model.depth_values),
        "seldepth_values": list(model.seldepth_values),
        "move_times_ms": list(model.move_times_ms),
        "wall_times_ms": list(model.wall_times_ms),
        "latency_deltas_ms": list(model.latency_deltas_ms),
        "latency_alerts": list(model.latency_alerts),
        "current_ply": model.current_ply,
        "sfen": model.sfen,
    }
    if model.generation is not None:
        dto["generation"] = model.generation
    if model.game_result is not None:
        dto["game_result"] = game_result_name(model.game_result)
    if model.time_control_black is not None:
        dto["time_control_black"] = json_serialize(model.time_control_black)
    if model.time_control_white is not None:
        dto["time_control_white"] = json_serialize(model.time_control_white)
    if model.meta is not None:
        dto["meta"] = dict(model.meta)
    if model.engine_status:
        dto["engine_status"] = _engine_status_dto(model)
    if model.clock.active is not None:
        dto["clock_active"] = model.clock.active
    if model.clock.black_remain_ms is not None:
        dto["black_remain_ms"] = model.clock.black_remain_ms
    if model.clock.white_remain_ms is not None:
        dto["white_remain_ms"] = model.clock.white_remain_ms
    if model.clock.started_at_ms is not None:
        dto["clock_started_at_ms"] = model.clock.started_at_ms
    if model.clock.occurred_at_ms is not None:
        dto["clock_occurred_at_ms"] = model.clock.occurred_at_ms
    return dto


def normalize_worker_snapshot_dto(
    snapshot: WorkerSnapshotModel | Mapping[str, JsonValue] | None,
) -> GameSnapshot | None:
    model = to_worker_snapshot_model(snapshot)
    if model is None:
        return None
    return to_worker_snapshot_dto(model)


def to_ws_state_diff(
    event: MoveProgressEvent | ClockStartEvent | ClockIncrementEvent, model: WorkerSnapshotModel
) -> MoveProgressDiffPayload | ClockStartDiffPayload | ClockIncrementDiffPayload:
    if event["type"] == "move_progress":
        payload: MoveProgressDiffPayload = {
            "type": "move_progress",
            "current_ply": model.current_ply,
            "game_id": event.get("game_id", model.game_id),
        }
        if (move := event.get("move")) is not None:
            payload["move"] = move
        if (ki2_move := event.get("ki2_move")) is not None:
            payload["ki2_move"] = ki2_move
        if (eval_cp := event.get("eval_cp")) is not None:
            payload["eval"] = eval_cp
        if (sfen := event.get("sfen")) is not None:
            payload["sfen"] = sfen
        if (depth := event.get("depth")) is not None:
            payload["depth"] = depth
        if (seldepth := event.get("seldepth")) is not None:
            payload["seldepth"] = seldepth
        if (nodes := event.get("nodes")) is not None:
            payload["nodes"] = nodes
        if (time_ms := event.get("time_ms")) is not None:
            payload["time_ms"] = time_ms
        if (wall_time_ms := event.get("wall_time_ms")) is not None:
            payload["wall_time_ms"] = wall_time_ms
        if (latency_ms := event.get("latency_ms")) is not None:
            payload["latency_ms"] = latency_ms
        latency_alert = event.get("is_latency_alert")
        if latency_alert is not None:
            payload["is_latency_alert"] = latency_alert
        if (game_result := event.get("game_result")) is not None:
            payload["game_result"] = game_result_name(game_result)
        return payload

    if event["type"] == "clock_start":
        payload: ClockStartDiffPayload = {
            "type": "clock_start",
            "game_id": event.get("game_id", model.game_id),
        }
        if (active := event.get("active")) is not None:
            payload["active"] = active
        if (black_remain_ms := event.get("black_remain_ms")) is not None:
            payload["black_remain_ms"] = black_remain_ms
        if (white_remain_ms := event.get("white_remain_ms")) is not None:
            payload["white_remain_ms"] = white_remain_ms
        if (started_at_ms := event.get("started_at_ms")) is not None:
            payload["started_at_ms"] = started_at_ms
        if (byoyomi_ms_black := event.get("byoyomi_ms_black")) is not None:
            payload["byoyomi_ms_black"] = byoyomi_ms_black
        if (byoyomi_ms_white := event.get("byoyomi_ms_white")) is not None:
            payload["byoyomi_ms_white"] = byoyomi_ms_white
        if (increment_ms_black := event.get("increment_ms_black")) is not None:
            payload["increment_ms_black"] = increment_ms_black
        if (increment_ms_white := event.get("increment_ms_white")) is not None:
            payload["increment_ms_white"] = increment_ms_white
        time_control_black = to_json_value(event.get("time_control_black"))
        if time_control_black is not None:
            payload["time_control_black"] = time_control_black
        time_control_white = to_json_value(event.get("time_control_white"))
        if time_control_white is not None:
            payload["time_control_white"] = time_control_white
        if (initial_sfen := event.get("initial_sfen")) is not None:
            payload["initial_sfen"] = initial_sfen
        return payload

    payload: ClockIncrementDiffPayload = {
        "type": "clock_increment",
        "game_id": event.get("game_id", model.game_id),
    }
    if (side := event.get("side")) is not None:
        payload["side"] = side
    if (applied_increment_ms := event.get("applied_increment_ms")) is not None:
        payload["applied_increment_ms"] = applied_increment_ms
    if (pre_black_remain_ms := event.get("pre_black_remain_ms")) is not None:
        payload["pre_black_remain_ms"] = pre_black_remain_ms
    if (pre_white_remain_ms := event.get("pre_white_remain_ms")) is not None:
        payload["pre_white_remain_ms"] = pre_white_remain_ms
    if (black_remain_ms := event.get("black_remain_ms")) is not None:
        payload["black_remain_ms"] = black_remain_ms
    if (white_remain_ms := event.get("white_remain_ms")) is not None:
        payload["white_remain_ms"] = white_remain_ms
    if (occurred_at_ms := event.get("occurred_at_ms")) is not None:
        payload["occurred_at_ms"] = occurred_at_ms
    time_control_black = to_json_value(event.get("time_control_black"))
    if time_control_black is not None:
        payload["time_control_black"] = time_control_black
    time_control_white = to_json_value(event.get("time_control_white"))
    if time_control_white is not None:
        payload["time_control_white"] = time_control_white
    return payload


def to_ws_moves_diff(event: HandshakeLogEvent | EngineIoEvent, model: WorkerSnapshotModel) -> EngineLogDiffPayload:
    payload: EngineLogDiffPayload = {
        "type": event["type"],
        "engine_status": _engine_status_compact_dto(model),
        "role": event["role"],
    }
    payload["game_id"] = event.get("game_id", model.game_id)
    if (line := event.get("line")) is not None:
        payload["line"] = line
    if (direction := event.get("direction")) is not None:
        payload["direction"] = direction
    if (ts := event.get("ts")) is not None:
        payload["ts"] = ts
    if (state := event.get("state")) is not None:
        payload["state"] = state
    return payload


def to_game_assigned_payload(event: GameAssignedEvent, model: WorkerSnapshotModel) -> GameAssignedDiffPayload:
    payload: GameAssignedDiffPayload = {"type": "game_assigned"}
    payload["game_id"] = event.get("game_id", model.game_id)
    payload["initial_sfen"] = event.get("initial_sfen", model.initial_sfen)
    payload["black_name"] = event.get("black_name", model.black_name)
    payload["white_name"] = event.get("white_name", model.white_name)
    if (engine_status := event.get("engine_status")) is not None:
        payload["engine_status"] = to_json_object(engine_status)
    else:
        payload["engine_status"] = to_json_object(_engine_status_dto(model))
    time_control_black = to_json_value(event.get("time_control_black"))
    if time_control_black is None:
        time_control_black = to_json_value(model.time_control_black)
    if time_control_black is not None:
        payload["time_control_black"] = time_control_black
    time_control_white = to_json_value(event.get("time_control_white"))
    if time_control_white is None:
        time_control_white = to_json_value(model.time_control_white)
    if time_control_white is not None:
        payload["time_control_white"] = time_control_white
    if model.clock.active is not None:
        payload["active"] = model.clock.active
    if model.clock.black_remain_ms is not None:
        payload["black_remain_ms"] = model.clock.black_remain_ms
    if model.clock.white_remain_ms is not None:
        payload["white_remain_ms"] = model.clock.white_remain_ms
    if model.clock.started_at_ms is not None:
        payload["started_at_ms"] = model.clock.started_at_ms
    return payload


__all__ = [
    "normalize_worker_snapshot_dto",
    "to_game_assigned_payload",
    "to_worker_snapshot_dto",
    "to_ws_moves_diff",
    "to_ws_state_diff",
]
