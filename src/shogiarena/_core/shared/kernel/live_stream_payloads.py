"""Canonical payload contracts for live dashboard stream diffs."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.snapshots import EngineStatusMap


class MoveProgressDiffPayload(TypedDict, total=False):
    type: Literal["move_progress"]
    game_id: str
    current_ply: int
    move: str
    ki2_move: str
    eval: int
    sfen: str
    depth: int
    seldepth: int
    nodes: int
    time_ms: int
    wall_time_ms: int
    latency_ms: int
    is_latency_alert: bool
    game_result: str


class ClockStartDiffPayload(TypedDict, total=False):
    type: Literal["clock_start"]
    game_id: str
    active: str
    black_remain_ms: int
    white_remain_ms: int
    started_at_ms: int
    initial_sfen: str
    time_control_black: JsonValue
    time_control_white: JsonValue
    byoyomi_ms_black: int
    byoyomi_ms_white: int
    increment_ms_black: int
    increment_ms_white: int


class ClockIncrementDiffPayload(TypedDict, total=False):
    type: Literal["clock_increment"]
    game_id: str
    side: str
    applied_increment_ms: int
    pre_black_remain_ms: int
    pre_white_remain_ms: int
    black_remain_ms: int
    white_remain_ms: int
    occurred_at_ms: int
    time_control_black: JsonValue
    time_control_white: JsonValue


class GameAssignedDiffPayload(TypedDict, total=False):
    type: Literal["game_assigned"]
    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    engine_status: JsonObject
    time_control_black: JsonValue
    time_control_white: JsonValue
    active: str
    black_remain_ms: int
    white_remain_ms: int
    started_at_ms: int


class EngineLogDiffPayload(TypedDict):
    type: Literal["handshake_log", "engine_io"]
    engine_status: EngineStatusMap
    role: str
    game_id: NotRequired[str]
    line: NotRequired[str]
    direction: NotRequired[str]
    ts: NotRequired[int]
    state: NotRequired[str]


LiveStreamDiffPayload = (
    MoveProgressDiffPayload
    | ClockStartDiffPayload
    | ClockIncrementDiffPayload
    | GameAssignedDiffPayload
    | EngineLogDiffPayload
)


__all__ = [
    "ClockIncrementDiffPayload",
    "ClockStartDiffPayload",
    "EngineLogDiffPayload",
    "GameAssignedDiffPayload",
    "LiveStreamDiffPayload",
    "MoveProgressDiffPayload",
]
