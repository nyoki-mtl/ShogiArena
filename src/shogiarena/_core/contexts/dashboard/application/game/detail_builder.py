"""Shared game detail payload builder for tournament and SPSA APIs."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from aiohttp import web
from rshogi.core import Board

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.ki2_notation import normalize_ki2_move_text
from shogiarena._core.shared.kernel.scalar_coercion.api import strict_int

_EvalValue = int | float | str | None


def _initial_turn(initial_sfen: str | None) -> str:
    if not initial_sfen or initial_sfen == "startpos":
        return "b"
    parts = initial_sfen.split()
    if len(parts) > 1 and parts[1] in {"b", "w"}:
        return parts[1]
    return "b"


def _is_mover_black(initial_turn: str, ply_index: int) -> bool:
    move_number = ply_index + 1
    if initial_turn == "b":
        return move_number % 2 == 1
    return move_number % 2 == 0


def _compute_eval_arrays(
    values: Sequence[_EvalValue] | None,
    initial_sfen: str | None,
    expected_length: int,
) -> tuple[list[float | None], list[float | None]]:
    """Normalize mover-centric eval history into black/white perspectives."""
    if values is None:
        return ([None] * expected_length, [None] * expected_length)

    initial_turn = _initial_turn(initial_sfen)
    eval_black: list[float | None] = []
    eval_white: list[float | None] = []
    for index, raw in enumerate(values):
        if raw is None:
            eval_black.append(None)
            eval_white.append(None)
            continue
        if not isinstance(raw, int | float):
            try:
                numeric = float(raw)
            except (TypeError, ValueError) as error:
                raise web.HTTPInternalServerError(reason="Game eval values must be numeric") from error
        else:
            numeric = float(raw)

        mover_is_black = _is_mover_black(initial_turn, index)
        if mover_is_black:
            eval_black.append(numeric)
            eval_white.append(-numeric)
        else:
            eval_black.append(-numeric)
            eval_white.append(numeric)

    if len(eval_black) < expected_length:
        padding = expected_length - len(eval_black)
        eval_black.extend([None] * padding)
        eval_white.extend([None] * padding)

    return (eval_black, eval_white)


def build_game_detail_payload(*, record: Any, game_id: str, logger: logging.Logger) -> JsonObject:
    """Build a normalized game detail payload from a shogidb record."""
    metadata = record.metadata
    game_id_value = record.game_name or game_id
    black_player = metadata.black_player or ""
    white_player = metadata.white_player or ""
    black_tc = record.black_time_control
    white_tc = record.white_time_control
    tc_black = black_tc.to_spec() if black_tc is not None else None
    tc_white = white_tc.to_spec() if white_tc is not None else None
    start_time = metadata.start_date
    end_time = metadata.end_date

    initial_sfen_raw = record.init_position_sfen
    initial_sfen = initial_sfen_raw if initial_sfen_raw != "startpos" else None
    board = Board()
    if initial_sfen:
        board.set_sfen(initial_sfen)
    moves_usi: list[str] = []
    moves_ki2: list[str] = []
    eval_values: list[int | None] = []
    nodes_values: list[int | None] = []
    depth_values: list[int | None] = []
    seldepth_values: list[int | None] = []
    move_times_ms: list[int | None] = []
    wall_times_ms: list[int | None] = []
    engine_wall_times_ms: list[int | None] = []
    latency_deltas_ms: list[int | None] = []
    for move_entry in record.moves:
        mv = move_entry.move
        move_time = move_entry.time_ms
        engine_info = move_entry.engine_info
        move_text = mv.to_usi()
        if not board.is_legal_move(mv):
            logger.error("Illegal move: %s", move_text)
            break
        moves_ki2.append(normalize_ki2_move_text(board.move32_from_move(mv).to_ki2(board) or mv.to_usi()))
        moves_usi.append(move_text)
        board.apply_move(mv)

        eval_cp = engine_info.eval if engine_info is not None else None
        nodes = engine_info.nodes if engine_info is not None else None
        depth = engine_info.depth if engine_info is not None else None
        seldepth = engine_info.seldepth if engine_info is not None else None
        wall_time = engine_info.wall_time_ms if engine_info is not None else None
        engine_wall_time = None
        if engine_info is not None:
            engine_wall_time = engine_info.extras.get("engine_wall_time_ms")
        latency_delta = engine_info.latency_delta_ms if engine_info is not None else None
        move_time_value = strict_int(move_time)
        eval_value = strict_int(eval_cp)
        nodes_value = strict_int(nodes)
        depth_value = strict_int(depth)
        seldepth_values.append(strict_int(seldepth))
        wall_time_value = strict_int(wall_time)
        engine_wall_time_value = strict_int(engine_wall_time)
        latency_deltas_ms.append(strict_int(latency_delta))
        move_times_ms.append(move_time_value)
        eval_values.append(eval_value)
        nodes_values.append(nodes_value)
        depth_values.append(depth_value)
        wall_times_ms.append(wall_time_value)
        engine_wall_times_ms.append(engine_wall_time_value)

    if not any(value is not None for value in move_times_ms):
        move_times_ms = []
    if not any(value is not None for value in wall_times_ms):
        wall_times_ms = []
    if not any(value is not None for value in engine_wall_times_ms):
        engine_wall_times_ms = []
    if not any(value is not None for value in latency_deltas_ms):
        latency_deltas_ms = []

    eval_black, eval_white = _compute_eval_arrays(
        eval_values,
        initial_sfen_raw,
        len(moves_usi),
    )

    result_obj = record.result
    return {
        "game_id": game_id_value,
        "black_player": black_player,
        "white_player": white_player,
        "game_result": result_obj.name if result_obj is not None else None,
        "initial_sfen": initial_sfen_raw,
        "time_control_black": tc_black,
        "time_control_white": tc_white,
        "moves": moves_usi,
        "ki2_moves": moves_ki2,
        "eval_black": eval_black,
        "eval_white": eval_white,
        "nodes_values": nodes_values,
        "depth_values": depth_values,
        "seldepth_values": seldepth_values,
        "move_times_ms": move_times_ms,
        "wall_times_ms": wall_times_ms,
        "engine_wall_times_ms": engine_wall_times_ms,
        "latency_deltas_ms": latency_deltas_ms,
        "total_plies": len(record.moves),
        "start_time": start_time,
        "end_time": end_time,
    }


__all__ = ["build_game_detail_payload"]
