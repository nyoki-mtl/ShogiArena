"""Internal snapshot models and normalization helpers for runtime orchestration."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from shogiarena._core.contexts.game_session.application.progress.events import ProgressEvent
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import (
    _coerce_str_list,
    coerce_bool_list,
    coerce_int_list,
    normalize_role,
    sanitize_engine_io_line_optional,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize


@dataclass(slots=True)
class EngineIoTailModel:
    dir: Literal["in", "out"]
    line: str
    ts: int
    state: str | None = None


@dataclass(slots=True)
class EngineStatusModel:
    state: str
    io_tail: list[EngineIoTailModel]
    updated_at_ms: int


def default_engine_status_model_entry(now_ms: int) -> EngineStatusModel:
    return EngineStatusModel(state="waiting_for_usiok", io_tail=[], updated_at_ms=now_ms)


@dataclass(slots=True)
class ClockModel:
    """Clock state for a game view."""

    active: Literal["black", "white"] | None = None
    black_remain_ms: int | None = None
    white_remain_ms: int | None = None
    started_at_ms: int | None = None
    occurred_at_ms: int | None = None


@dataclass(slots=True)
class WorkerSnapshotModel:
    """Internal worker snapshot normalized model."""

    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    current_ply: int
    sfen: str

    moves: list[str] = field(default_factory=list)
    ki2_moves: list[str] = field(default_factory=list)
    eval_black: list[int] = field(default_factory=list)
    eval_white: list[int] = field(default_factory=list)
    nodes_values: list[int] = field(default_factory=list)
    depth_values: list[int] = field(default_factory=list)
    seldepth_values: list[int] = field(default_factory=list)
    move_times_ms: list[int] = field(default_factory=list)
    wall_times_ms: list[int] = field(default_factory=list)
    latency_deltas_ms: list[int] = field(default_factory=list)
    latency_alerts: list[bool] = field(default_factory=list)

    game_result: GameResult | None = None
    clock: ClockModel = field(default_factory=ClockModel)
    generation: int | None = None
    time_control_black: JsonValue | None = None
    time_control_white: JsonValue | None = None
    meta: JsonObject | None = None
    engine_status: dict[str, EngineStatusModel] = field(default_factory=dict)

    def reset_for_new_game(
        self,
        game_id: str,
        initial_sfen: str,
        black_name: str,
        white_name: str,
        time_control_black: JsonValue | None,
        time_control_white: JsonValue | None,
        generation: int,
    ) -> None:
        self.game_id = game_id
        self.initial_sfen = initial_sfen
        self.black_name = black_name
        self.white_name = white_name
        self.current_ply = 0
        self.sfen = initial_sfen
        self.moves.clear()
        self.ki2_moves.clear()
        self.eval_black.clear()
        self.eval_white.clear()
        self.nodes_values.clear()
        self.depth_values.clear()
        self.seldepth_values.clear()
        self.move_times_ms.clear()
        self.wall_times_ms.clear()
        self.latency_deltas_ms.clear()
        self.latency_alerts.clear()
        self.game_result = None
        self.time_control_black = time_control_black
        self.time_control_white = time_control_white
        self.clock = ClockModel()
        self.meta = None
        self.generation = generation
        self.engine_status = {}


def _to_json_mapping(source: object) -> dict[str, JsonValue]:
    if not isinstance(source, Mapping):
        return {}
    return {str(key): json_serialize(value) for key, value in source.items()}


def _engine_status_model_from_mapping(raw: Mapping[str, JsonValue]) -> EngineStatusModel:
    state = coerce_str(raw.get("state")) or "waiting_for_usiok"
    updated_at_ms = coerce_int(raw.get("updated_at_ms"))
    if updated_at_ms is None:
        updated_at_ms = int(time.time() * 1000)
    tails_raw = raw.get("io_tail")
    tails: list[EngineIoTailModel] = []
    if isinstance(tails_raw, list):
        for item in tails_raw:
            if not isinstance(item, Mapping):
                continue
            item_mapping = _to_json_mapping(item)
            direction = coerce_str(item_mapping.get("dir"))
            if direction not in {"in", "out"}:
                continue
            normalized_direction: Literal["in", "out"] = "in" if direction == "in" else "out"
            line = sanitize_engine_io_line_optional(coerce_str(item_mapping.get("line")))
            if line is None:
                continue
            ts = coerce_int(item_mapping.get("ts"))
            if ts is None:
                ts = int(time.time() * 1000)
            tails.append(
                EngineIoTailModel(
                    dir=normalized_direction,
                    line=line,
                    ts=ts,
                    state=coerce_str(item_mapping.get("state")),
                )
            )
    return EngineStatusModel(state=state, io_tail=tails, updated_at_ms=updated_at_ms)


def _worker_snapshot_model_from_mapping(raw: Mapping[str, JsonValue]) -> WorkerSnapshotModel:
    initial_sfen = coerce_str(raw.get("initial_sfen"))
    if not initial_sfen:
        raise ValueError("snapshot missing initial_sfen")
    game_id = coerce_str(raw.get("game_id")) or ""
    black_name = coerce_str(raw.get("black_name")) or "Unknown"
    white_name = coerce_str(raw.get("white_name")) or "Unknown"
    current_ply = coerce_int(raw.get("current_ply"))
    meta_raw = raw.get("meta")
    meta: dict[str, JsonValue] | None = None
    if isinstance(meta_raw, Mapping):
        meta = _to_json_mapping(meta_raw)
    generation_value = coerce_int(raw.get("generation"))
    game_result = None
    if "game_result" in raw:
        game_result = coerce_game_result(raw.get("game_result"), is_strict=True)
    model = WorkerSnapshotModel(
        game_id=game_id,
        initial_sfen=initial_sfen,
        black_name=black_name,
        white_name=white_name,
        current_ply=current_ply or len(_coerce_str_list(raw.get("moves"))),
        sfen=coerce_str(raw.get("sfen")) or initial_sfen,
        moves=_coerce_str_list(raw.get("moves")),
        ki2_moves=_coerce_str_list(raw.get("ki2_moves")),
        eval_black=coerce_int_list(raw.get("eval_black")),
        eval_white=coerce_int_list(raw.get("eval_white")),
        nodes_values=coerce_int_list(raw.get("nodes_values")),
        depth_values=coerce_int_list(raw.get("depth_values")),
        seldepth_values=coerce_int_list(raw.get("seldepth_values")),
        move_times_ms=coerce_int_list(raw.get("move_times_ms")),
        wall_times_ms=coerce_int_list(raw.get("wall_times_ms")),
        latency_deltas_ms=coerce_int_list(raw.get("latency_deltas_ms")),
        latency_alerts=coerce_bool_list(raw.get("latency_alerts")),
        game_result=game_result,
        clock=ClockModel(
            active=normalize_role(coerce_str(raw.get("clock_active"))),
            black_remain_ms=coerce_int(raw.get("black_remain_ms")),
            white_remain_ms=coerce_int(raw.get("white_remain_ms")),
            started_at_ms=coerce_int(raw.get("clock_started_at_ms")),
        ),
        generation=generation_value,
        time_control_black=raw.get("time_control_black"),
        time_control_white=raw.get("time_control_white"),
        meta=meta,
    )
    status_raw = raw.get("engine_status")
    if isinstance(status_raw, Mapping):
        status_mapping = _to_json_mapping(status_raw)
        model.engine_status = {}
        for role in ("black", "white"):
            role_obj = status_mapping.get(role)
            if isinstance(role_obj, Mapping):
                model.engine_status[role] = _engine_status_model_from_mapping(_to_json_mapping(role_obj))
    return model


def snapshot_from_progress_event(
    event: ProgressEvent,
    *,
    generation: int,
    name_default: str = "",
) -> WorkerSnapshotModel:
    initial_sfen = coerce_str(event.get("initial_sfen"))
    if not initial_sfen:
        raise ValueError("progress payload missing required initial_sfen")
    game_id = coerce_str(event.get("game_id")) or ""
    clock = ClockModel()
    if event["type"] == "clock_start":
        active = normalize_role(coerce_str(event.get("active")))
        clock.active = active
        clock.black_remain_ms = coerce_int(event.get("black_remain_ms"))
        clock.white_remain_ms = coerce_int(event.get("white_remain_ms"))
        clock.started_at_ms = coerce_int(event.get("started_at_ms"))
    return WorkerSnapshotModel(
        game_id=game_id,
        initial_sfen=initial_sfen,
        black_name=coerce_str(event.get("black_name")) or name_default,
        white_name=coerce_str(event.get("white_name")) or name_default,
        current_ply=0,
        sfen=initial_sfen,
        time_control_black=json_serialize(event.get("time_control_black")),
        time_control_white=json_serialize(event.get("time_control_white")),
        generation=generation,
        clock=clock,
    )


def to_worker_snapshot_model(
    snapshot: WorkerSnapshotModel | Mapping[str, JsonValue] | None,
) -> WorkerSnapshotModel | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, WorkerSnapshotModel):
        return snapshot
    if isinstance(snapshot, Mapping):
        return _worker_snapshot_model_from_mapping(snapshot)
    return None


__all__ = [
    "ClockModel",
    "EngineIoTailModel",
    "EngineStatusModel",
    "WorkerSnapshotModel",
    "default_engine_status_model_entry",
    "snapshot_from_progress_event",
    "to_worker_snapshot_model",
]
