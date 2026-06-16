"""Helpers for progress payload parsing and queueing."""

from __future__ import annotations

import asyncio
import json
import zlib
from collections.abc import Mapping

from shogiarena._core.contexts.game_session.application.progress.events import MoveProgressEvent, parse_progress_event
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int as _coerce_int


def _determine_event_move_count(event: Mapping[str, JsonValue], fallback_move_count: int) -> int:
    """Determine the move count used for dashboard progress routing."""
    event_type = event.get("type")
    if event_type == "move_progress":
        try:
            return _coerce_int(event.get("ply")) or 0
        except (TypeError, ValueError):
            return fallback_move_count
    return fallback_move_count


def enqueue_progress_event(
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
    game_id: str,
    event: Mapping[str, JsonValue],
    *,
    fallback_move_count: int = 0,
    should_allow_default_str: bool = False,
) -> None:
    """Serialize a remote event and enqueue it for dashboard progress."""
    if progress_queue is None:
        return
    move_count = _determine_event_move_count(event, fallback_move_count)
    if should_allow_default_str:
        payload = json.dumps(event, ensure_ascii=False, default=str)
    else:
        payload = json.dumps(event, ensure_ascii=False)
    progress_queue.put_nowait((_numeric_game_id(game_id), move_count, payload))


def parse_move_progress(raw: Mapping[str, JsonValue]) -> MoveProgressEvent:
    """Parse and validate raw move_progress payload."""
    payload = dict(raw)
    payload.setdefault("type", "move_progress")
    parsed = parse_progress_event(payload)
    if parsed["type"] != "move_progress":
        raise ValueError("payload type mismatch for parse_move_progress")
    event: MoveProgressEvent = {"type": "move_progress"}
    for key in (
        "game_id",
        "initial_sfen",
        "black_name",
        "white_name",
        "move",
        "ki2_move",
        "eval_cp",
        "ply",
        "sfen",
        "nodes",
        "depth",
        "seldepth",
        "time_ms",
        "wall_time_ms",
        "engine_wall_time_ms",
        "latency_ms",
        "is_latency_alert",
        "game_result",
    ):
        value = parsed.get(key)
        if value is not None:
            event[key] = value
    return event


def _numeric_game_id(game_id: str | int) -> int:
    if isinstance(game_id, int):
        return game_id
    if isinstance(game_id, str) and game_id.startswith("game_"):
        return int(game_id.split("_", 1)[1])
    return zlib.crc32(str(game_id).encode("utf-8")) & 0x7FFFFFFF


__all__ = [
    "enqueue_progress_event",
    "parse_move_progress",
]
