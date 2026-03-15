"""Builder for converting remote progress streams into GameRecord."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import rshogi.record
from rshogi.core import Board, Move, normalize_usi_position

from shogiarena._core.shared.kernel.game_results import STARTING_SFEN, GameResult, game_result_terminal_kind
from shogiarena._core.shared.kernel.time_control import TimeControlLimitsPort
from shogiarena._core.shared.kernel.time_control_spec import limits_to_record_time_spec


def build_remote_game_info(
    *,
    start_sfen: str,
    moves: Sequence[str],
    game_result: GameResult | None,
    game_id: str,
    black_name: str,
    white_name: str,
    black_limits: TimeControlLimitsPort,
    white_limits: TimeControlLimitsPort,
    move_times: Sequence[int | None] | None = None,
    wall_times: Sequence[int | None] | None = None,
    latency_deltas: Sequence[int | None] | None = None,
    nodes: Sequence[int | None] | None = None,
    depth: Sequence[int | None] | None = None,
    seldepth: Sequence[int | None] | None = None,
    evals: Sequence[int | None] | None = None,
) -> rshogi.record.GameRecord:
    """Construct ``GameRecord`` from remote move_progress streams."""

    raw_moves = list(moves)
    resolved_moves: list[Move] = []
    board = Board()
    normalized_sfen = normalize_usi_position(start_sfen)
    if normalized_sfen != "startpos":
        board.set_sfen(normalized_sfen)
    for move in raw_moves:
        try:
            mv = Move.from_usi(move)
        except ValueError:
            resolved_move = None
        else:
            resolved_move = mv if board.is_legal_move(mv) else None
        if resolved_move is None:
            raise RuntimeError(f"Illegal move in final payload: {move}")
        resolved_moves.append(resolved_move)
        board.apply_move(resolved_move)
    if game_result is None:
        raise RuntimeError("Missing game_result in remote move_progress events")

    num_plies = len(resolved_moves)

    def _pad(seq: Sequence[int | None] | None) -> list[int | None] | None:
        if seq is None:
            return None
        data = list(seq)
        while len(data) < num_plies:
            data.append(None)
        return data[:num_plies]

    move_times_core = _pad(move_times)
    wall_times_core = _pad(wall_times)
    latency_core = _pad(latency_deltas)
    nodes_core = _pad(nodes)
    depth_core = _pad(depth)
    seldepth_core = _pad(seldepth)
    eval_core = _pad(evals)

    now_iso = datetime.now().isoformat()
    tc_black_str = limits_to_record_time_spec(black_limits)
    tc_white_str = limits_to_record_time_spec(white_limits)
    record_metadata = rshogi.record.GameRecordMetadata(
        game_name=game_id,
        game_type="arena",
        black_player=black_name,
        white_player=white_name,
        start_date=now_iso,
        end_date=now_iso,
        updated_date=now_iso,
        black_time_control=rshogi.record.TimeControl.from_spec(tc_black_str),
        white_time_control=rshogi.record.TimeControl.from_spec(tc_white_str),
        attributes={
            "game_name": game_id,
            "game_type": "arena",
            "updated_date": now_iso,
        },
    )
    move_records: list[rshogi.record.MoveRecord] = []
    for idx, move in enumerate(resolved_moves):
        wall_time = wall_times_core[idx] if wall_times_core is not None else None
        latency_delta = latency_core[idx] if latency_core is not None else None
        engine_info = rshogi.record.MoveEngineInfo(
            eval=eval_core[idx] if eval_core is not None else None,
            nodes=nodes_core[idx] if nodes_core is not None else None,
            depth=depth_core[idx] if depth_core is not None else None,
            seldepth=seldepth_core[idx] if seldepth_core is not None else None,
            wall_time_ms=int(wall_time) if wall_time is not None else None,
            latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
        )
        move_records.append(
            rshogi.record.MoveRecord(
                move,
                time_ms=move_times_core[idx] if move_times_core is not None else None,
                engine_info=engine_info,
            )
        )
    init_sfen = normalized_sfen if normalized_sfen != "startpos" else STARTING_SFEN
    terminal = rshogi.record.SpecialMoveRecord(game_result_terminal_kind(game_result), game_result)
    return rshogi.record.GameRecord.from_main_line(init_sfen, move_records, terminal, record_metadata)


__all__ = ["build_remote_game_info"]
