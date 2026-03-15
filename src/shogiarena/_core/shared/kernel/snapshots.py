"""Shared dashboard snapshot DTO types."""

from __future__ import annotations

from typing import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


class _EngineIoTailEntryRequired(TypedDict):
    dir: str
    line: str
    ts: int


class EngineIoTailEntry(_EngineIoTailEntryRequired, total=False):
    state: str


class EngineStatusEntry(TypedDict):
    state: str
    io_tail: list[EngineIoTailEntry]
    updated_at_ms: int


EngineStatusMap = dict[str, EngineStatusEntry]
EngineOptionsSnapshots = dict[str, dict[str, JsonValue]]
EngineInfoSnapshots = dict[str, dict[str, str]]


class GameSnapshot(TypedDict, total=False):
    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    moves: list[str]
    ki2_moves: list[str]
    eval_black: list[int]
    eval_white: list[int]
    nodes_values: list[int]
    depth_values: list[int]
    seldepth_values: list[int]
    move_times_ms: list[int]
    wall_times_ms: list[int]
    latency_deltas_ms: list[int]
    latency_alerts: list[bool]
    current_ply: int
    sfen: str
    generation: int
    engine_status: EngineStatusMap
    game_result: str
    clock_active: str
    black_remain_ms: int
    white_remain_ms: int
    clock_started_at_ms: int
    clock_occurred_at_ms: int
    clock: JsonObject
    meta: JsonObject
    time_control_black: object | None
    time_control_white: object | None


__all__ = [
    "EngineInfoSnapshots",
    "EngineIoTailEntry",
    "EngineOptionsSnapshots",
    "EngineStatusEntry",
    "EngineStatusMap",
    "GameSnapshot",
]
