"""Remote game move/event aggregation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int


def update_remote_move_aggregates(
    event: Mapping[str, JsonValue],
    *,
    last_ply_seen: int,
    moves: list[str],
    evals: list[int | None],
    nodes: list[int | None],
    depth: list[int | None],
    seldepth: list[int | None],
    move_times: list[int | None],
    wall_times: list[int | None],
    engine_wall_times: list[int | None],
) -> int:
    """Update remote-move aggregate buffers from a streamed event.

    Returns the updated ``last_ply_seen``.
    """
    if event.get("type") == "move_progress":
        move = event.get("move")
        if isinstance(move, str) and move.strip():
            # Only move-carrying events participate in ply de-dup, and last_ply_seen advances
            # only when a move is actually appended. Otherwise a move-less terminal event at the
            # same ply would block a later real move at that ply. The remote stream is not
            # guaranteed to be strictly ordered or de-duplicated, so skip non-advancing plies.
            last_ply_val = coerce_int(event.get("ply")) or 0
            if last_ply_val <= last_ply_seen:
                return last_ply_seen
            last_ply_seen = last_ply_val
            moves.append(move)
            evals.append(coerce_int(event.get("eval_cp")))
            nodes.append(coerce_int(event.get("nodes")))
            depth.append(coerce_int(event.get("depth")))
            seldepth.append(coerce_int(event.get("seldepth")))
            move_times.append(coerce_int(event.get("time_ms")))
            wall_times.append(coerce_int(event.get("wall_time_ms")))
            engine_wall_times.append(coerce_int(event.get("engine_wall_time_ms")))
    return last_ply_seen


def extract_final_game_result(events: Sequence[JsonObject]) -> GameResult:
    """Extract the final ``GameResult`` from streamed remote events."""
    final_result = next(
        (
            coerce_game_result(entry.get("game_result"), is_strict=True)
            for entry in reversed(events)
            if isinstance(entry, dict) and entry.get("game_result") is not None
        ),
        None,
    )
    if final_result is None:
        raise RuntimeError("Remote game did not produce a terminal game_result in move_progress events")
    return final_result


__all__ = ["extract_final_game_result", "update_remote_move_aggregates"]
