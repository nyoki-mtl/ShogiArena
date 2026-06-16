"""Mutation helpers for worker snapshot progress state."""

from __future__ import annotations

import time
from typing import Literal

from shogiarena._core.contexts.game_session.application.progress.engine_io_limits import ENGINE_IO_TAIL_LIMIT
from shogiarena._core.contexts.game_session.application.progress.events import (
    ClockIncrementEvent,
    ClockStartEvent,
    GameAssignedEvent,
    MoveProgressEvent,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    EngineIoTailModel,
    EngineStatusModel,
    WorkerSnapshotModel,
    default_engine_status_model_entry,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_normalizer import (
    normalize_role,
    sanitize_engine_io_line_optional,
)
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize


def ensure_engine_status_model(snapshot: WorkerSnapshotModel) -> dict[str, EngineStatusModel]:
    now_ms = int(time.time() * 1000)
    status = snapshot.engine_status
    for role in ("black", "white"):
        if role not in status:
            status[role] = default_engine_status_model_entry(now_ms)
    return status


def _append_engine_io_tail(
    entry: EngineStatusModel,
    *,
    direction: Literal["in", "out"] | None,
    line: str | None,
    ts: int,
) -> None:
    if direction is None or not line:
        return
    entry.io_tail.append(EngineIoTailModel(dir=direction, line=line, ts=ts))
    if len(entry.io_tail) > ENGINE_IO_TAIL_LIMIT:
        del entry.io_tail[0 : len(entry.io_tail) - ENGINE_IO_TAIL_LIMIT]


def update_engine_status_model_entry(
    snapshot: WorkerSnapshotModel,
    role: str | None,
    *,
    direction: str | None,
    line: str | None,
    state: str | None,
    timestamp: int | None,
) -> bool:
    normalized_role = normalize_role(role)
    if normalized_role is None:
        return False
    status = ensure_engine_status_model(snapshot)
    entry = status[normalized_role]
    if state is not None:
        entry.state = state
    ts = int(timestamp if timestamp is not None else time.time() * 1000)
    normalized_direction: Literal["in", "out"] | None = None
    candidate_direction = coerce_str(direction)
    if candidate_direction in {"in", "out"}:
        normalized_direction = "in" if candidate_direction == "in" else "out"
    _append_engine_io_tail(
        entry,
        direction=normalized_direction,
        line=sanitize_engine_io_line_optional(line),
        ts=ts,
    )
    entry.updated_at_ms = ts
    return True


def apply_clock_start_model(snapshot: WorkerSnapshotModel, event: ClockStartEvent) -> None:
    snapshot.clock.active = normalize_role(coerce_str(event.get("active")))
    snapshot.clock.black_remain_ms = event.get("black_remain_ms")
    snapshot.clock.white_remain_ms = event.get("white_remain_ms")
    snapshot.clock.started_at_ms = event.get("started_at_ms")
    snapshot.clock.occurred_at_ms = None
    if event.get("time_control_black") is not None:
        snapshot.time_control_black = json_serialize(event.get("time_control_black"))
    if event.get("time_control_white") is not None:
        snapshot.time_control_white = json_serialize(event.get("time_control_white"))


def apply_clock_increment_model(snapshot: WorkerSnapshotModel, event: ClockIncrementEvent) -> None:
    snapshot.clock.active = None
    snapshot.clock.black_remain_ms = event.get("black_remain_ms")
    snapshot.clock.white_remain_ms = event.get("white_remain_ms")
    snapshot.clock.occurred_at_ms = event.get("occurred_at_ms")


def apply_game_assigned_model(
    snapshot: WorkerSnapshotModel,
    event: GameAssignedEvent,
    *,
    generation: int,
) -> bool:
    same_game = str(snapshot.game_id) == str(event.get("game_id", "")) if event.get("game_id") is not None else True
    if not same_game:
        snapshot.reset_for_new_game(
            game_id=coerce_str(event.get("game_id")) or "",
            initial_sfen=coerce_str(event.get("initial_sfen")) or snapshot.initial_sfen,
            black_name=coerce_str(event.get("black_name")) or snapshot.black_name,
            white_name=coerce_str(event.get("white_name")) or snapshot.white_name,
            time_control_black=json_serialize(event.get("time_control_black")),
            time_control_white=json_serialize(event.get("time_control_white")),
            generation=generation,
        )

    status = ensure_engine_status_model(snapshot)
    now_ms = int(time.time() * 1000)
    for role in ("black", "white"):
        entry = status[role]
        entry_tail_empty = len(entry.io_tail) == 0
        state_str = (entry.state or "").strip().lower()
        should_mark_queued = not same_game or (state_str in {"", "waiting_for_usiok", "queued"} and entry_tail_empty)
        if should_mark_queued:
            entry.state = "queued"
            entry.io_tail = []
            entry.updated_at_ms = now_ms
        else:
            if not entry.state:
                entry.state = "queued"
            if not entry.io_tail:
                entry.io_tail = []
            if entry.updated_at_ms <= 0:
                entry.updated_at_ms = now_ms

    if event.get("time_control_black") is not None:
        snapshot.time_control_black = json_serialize(event.get("time_control_black"))
    if event.get("time_control_white") is not None:
        snapshot.time_control_white = json_serialize(event.get("time_control_white"))
    return same_game


def apply_move_progress_model(snapshot: WorkerSnapshotModel, event: MoveProgressEvent) -> None:
    if event_move := event.get("move"):
        snapshot.moves.append(event_move)
    if event_ki2 := event.get("ki2_move"):
        snapshot.ki2_moves.append(event_ki2)

    eval_cp = event.get("eval_cp")
    if eval_cp is not None:
        mover_is_black = True
        if snapshot.moves:
            starts_black = (
                (snapshot.initial_sfen.strip() or "startpos").split(" ")[1] == "b"
                if len((snapshot.initial_sfen.strip() or "startpos").split(" ")) >= 2
                else True
            )
            mover_is_black = (len(snapshot.moves) % 2 == 1) if starts_black else (len(snapshot.moves) % 2 == 0)
        if mover_is_black:
            snapshot.eval_black.append(eval_cp)
            snapshot.eval_white.append(-eval_cp)
        else:
            snapshot.eval_black.append(-eval_cp)
            snapshot.eval_white.append(eval_cp)

    for key in (
        ("nodes", snapshot.nodes_values),
        ("depth", snapshot.depth_values),
        ("seldepth", snapshot.seldepth_values),
        ("time_ms", snapshot.move_times_ms),
        ("wall_time_ms", snapshot.wall_times_ms),
        ("engine_wall_time_ms", snapshot.engine_wall_times_ms),
        ("latency_ms", snapshot.latency_deltas_ms),
    ):
        value = event.get(key[0])
        if value is not None:
            key[1].append(int(value))

    latency_alert = event.get("is_latency_alert")
    if latency_alert is not None:
        snapshot.latency_alerts.append(bool(latency_alert))

    game_result = event.get("game_result")
    if game_result is not None:
        snapshot.game_result = game_result

    if sfen := event.get("sfen"):
        snapshot.sfen = sfen

    snapshot.current_ply = len(snapshot.moves)

    status = ensure_engine_status_model(snapshot)
    now_ms = int(time.time() * 1000)
    for entry in status.values():
        if entry.state not in {"ready", "thinking"}:
            entry.state = "ready"
            entry.updated_at_ms = now_ms


__all__ = [
    "apply_clock_increment_model",
    "apply_clock_start_model",
    "apply_game_assigned_model",
    "apply_move_progress_model",
    "ensure_engine_status_model",
    "update_engine_status_model_entry",
]
