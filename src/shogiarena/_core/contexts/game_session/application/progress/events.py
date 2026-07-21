"""Progress event schemas and parser for runtime orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NotRequired, TypeAlias, TypedDict

from typing_extensions import ReadOnly

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int, coerce_str


class _EventType(TypedDict):
    # ReadOnly にすることでサブクラスが Literal へ絞り込める（PEP 705）。
    # 可変フィールドのままだと TypedDict 継承時の再宣言は仕様違反になる。
    type: ReadOnly[str]


class MoveProgressEvent(_EventType):
    type: Literal["move_progress"]
    game_id: NotRequired[str]
    initial_sfen: NotRequired[str]
    black_name: NotRequired[str]
    white_name: NotRequired[str]
    move: NotRequired[str]
    ki2_move: NotRequired[str]
    eval_cp: NotRequired[int]
    ply: NotRequired[int]
    sfen: NotRequired[str]
    nodes: NotRequired[int]
    depth: NotRequired[int]
    seldepth: NotRequired[int]
    time_ms: NotRequired[int]
    wall_time_ms: NotRequired[int]
    engine_wall_time_ms: NotRequired[int]
    latency_ms: NotRequired[int]
    is_latency_alert: NotRequired[bool]
    game_result: NotRequired[GameResult]


class ClockStartEvent(_EventType):
    type: Literal["clock_start"]
    game_id: NotRequired[str]
    active: NotRequired[str]
    black_remain_ms: NotRequired[int]
    white_remain_ms: NotRequired[int]
    started_at_ms: NotRequired[int]
    initial_sfen: NotRequired[str]
    black_name: NotRequired[str]
    white_name: NotRequired[str]
    time_control_black: NotRequired[object]
    time_control_white: NotRequired[object]
    byoyomi_ms_black: NotRequired[int]
    byoyomi_ms_white: NotRequired[int]
    increment_ms_black: NotRequired[int]
    increment_ms_white: NotRequired[int]


class ClockIncrementEvent(_EventType):
    type: Literal["clock_increment"]
    game_id: NotRequired[str]
    side: NotRequired[str]
    applied_increment_ms: NotRequired[int]
    pre_black_remain_ms: NotRequired[int]
    pre_white_remain_ms: NotRequired[int]
    black_remain_ms: NotRequired[int]
    white_remain_ms: NotRequired[int]
    occurred_at_ms: NotRequired[int]


class HandshakeLogEvent(_EventType):
    type: Literal["handshake_log"]
    game_id: NotRequired[str]
    role: str
    direction: NotRequired[str]
    line: NotRequired[str]
    ts: NotRequired[int]
    state: NotRequired[str]


class EngineIoEvent(_EventType):
    type: Literal["engine_io"]
    game_id: NotRequired[str]
    role: str
    direction: NotRequired[str]
    line: NotRequired[str]
    ts: NotRequired[int]
    state: NotRequired[str]


class GameAssignedEvent(_EventType):
    type: Literal["game_assigned"]
    game_id: NotRequired[str]
    initial_sfen: NotRequired[str]
    black_name: NotRequired[str]
    white_name: NotRequired[str]
    time_control_black: NotRequired[object]
    time_control_white: NotRequired[object]
    engine_status: NotRequired[dict[str, object]]


ProgressEvent: TypeAlias = (
    MoveProgressEvent | ClockStartEvent | ClockIncrementEvent | HandshakeLogEvent | EngineIoEvent | GameAssignedEvent
)


def _to_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return value != 0
    return False


def parse_progress_event(raw: Mapping[str, JsonValue]) -> ProgressEvent:
    event_type = coerce_str(raw.get("type"))
    if event_type is None:
        raise ValueError("progress event must include required 'type'")

    if event_type == "move_progress":
        event: MoveProgressEvent = {"type": "move_progress"}
        for key in ("game_id", "initial_sfen", "black_name", "white_name", "move", "ki2_move", "sfen"):
            value = coerce_str(raw.get(key))
            if value is not None:
                event[key] = value
        for key in (
            "eval_cp",
            "ply",
            "nodes",
            "depth",
            "seldepth",
            "time_ms",
            "wall_time_ms",
            "engine_wall_time_ms",
            "latency_ms",
        ):
            value = coerce_int(raw.get(key))
            if value is not None:
                event[key] = value
        if "game_result" in raw:
            game_result = coerce_game_result(raw.get("game_result"), is_strict=True)
            event["game_result"] = game_result
        raw_latency_alert = raw.get("is_latency_alert")
        if raw_latency_alert is not None:
            event["is_latency_alert"] = _to_bool(raw_latency_alert)
        return event

    if event_type == "clock_start":
        event: ClockStartEvent = {"type": "clock_start"}
        for key in ("game_id", "initial_sfen", "black_name", "white_name"):
            value = coerce_str(raw.get(key))
            if value is not None:
                event[key] = value
        active = coerce_str(raw.get("active"))
        if active is not None:
            event["active"] = active
        for key in (
            "black_remain_ms",
            "white_remain_ms",
            "started_at_ms",
            "byoyomi_ms_black",
            "byoyomi_ms_white",
            "increment_ms_black",
            "increment_ms_white",
        ):
            value = coerce_int(raw.get(key))
            if value is not None:
                event[key] = value
        for key in ("time_control_black", "time_control_white"):
            if key in raw:
                event[key] = raw[key]
        return event

    if event_type == "clock_increment":
        event: ClockIncrementEvent = {"type": "clock_increment"}
        game_id = coerce_str(raw.get("game_id"))
        if game_id is not None:
            event["game_id"] = game_id
        side = coerce_str(raw.get("side"))
        if side is not None:
            event["side"] = side
        for key in (
            "applied_increment_ms",
            "pre_black_remain_ms",
            "pre_white_remain_ms",
            "black_remain_ms",
            "white_remain_ms",
            "occurred_at_ms",
        ):
            value = coerce_int(raw.get(key))
            if value is not None:
                event[key] = value
        return event

    if event_type == "handshake_log":
        role = coerce_str(raw.get("role"))
        if role is None:
            raise ValueError("handshake_log event requires role")
        event: HandshakeLogEvent = {"type": "handshake_log", "role": role}
        for key in ("game_id", "direction", "line", "state"):
            value = coerce_str(raw.get(key))
            if value is not None:
                event[key] = value
        ts = coerce_int(raw.get("ts"))
        if ts is not None:
            event["ts"] = ts
        return event

    if event_type == "engine_io":
        role = coerce_str(raw.get("role"))
        if role is None:
            raise ValueError("engine_io event requires role")
        event: EngineIoEvent = {"type": "engine_io", "role": role}
        for key in ("game_id", "direction", "line", "state"):
            value = coerce_str(raw.get(key))
            if value is not None:
                event[key] = value
        ts = coerce_int(raw.get("ts"))
        if ts is not None:
            event["ts"] = ts
        return event

    if event_type == "game_assigned":
        event: GameAssignedEvent = {"type": "game_assigned"}
        for key in ("game_id", "initial_sfen", "black_name", "white_name"):
            value = coerce_str(raw.get(key))
            if value is not None:
                event[key] = value
        if not event.get("initial_sfen"):
            raise ValueError("game_assigned event requires initial_sfen")
        for key in ("time_control_black", "time_control_white"):
            if key in raw:
                event[key] = raw[key]
        engine_status = raw.get("engine_status")
        if isinstance(engine_status, Mapping):
            event["engine_status"] = {str(key): value for key, value in engine_status.items()}
        return event

    raise ValueError(f"unsupported progress event type: {event_type}")


__all__ = [
    "ClockIncrementEvent",
    "ClockStartEvent",
    "EngineIoEvent",
    "GameAssignedEvent",
    "HandshakeLogEvent",
    "MoveProgressEvent",
    "ProgressEvent",
    "parse_progress_event",
]
