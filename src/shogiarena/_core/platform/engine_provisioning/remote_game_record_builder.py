"""Builder for converting remote progress streams into GameRecord."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import rshogi.record
from rshogi.core import normalize_usi_position

from shogiarena._core.shared.kernel.game_results import GameResult
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
    engine_wall_times: Sequence[int | None] | None = None,
    latency_deltas: Sequence[int | None] | None = None,
    nodes: Sequence[int | None] | None = None,
    depth: Sequence[int | None] | None = None,
    seldepth: Sequence[int | None] | None = None,
    evals: Sequence[int | None] | None = None,
) -> rshogi.record.GameRecord:
    """Construct ``GameRecord`` from remote move_progress streams."""

    raw_moves = list(moves)
    normalized_sfen = normalize_usi_position(start_sfen)
    if game_result is None:
        raise RuntimeError("Missing game_result in remote move_progress events")

    def _to_list(seq: Sequence[int | None] | None) -> list[int | None] | None:
        if seq is None:
            return None
        return list(seq)

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
    try:
        record = rshogi.record.GameRecord.from_usi_main_line(
            normalized_sfen,
            raw_moves,
            result=game_result,
            move_times_ms=_to_list(move_times),
            evals=_to_list(evals),
            nodes=_to_list(nodes),
            depths=_to_list(depth),
            seldepths=_to_list(seldepth),
            wall_times_ms=_to_list(wall_times),
            latency_deltas_ms=_to_list(latency_deltas),
            metadata=record_metadata,
        )
        if engine_wall_times is not None:
            for move_record, engine_wall_time_ms in zip(record.moves, engine_wall_times, strict=False):
                engine_info = move_record.engine_info
                if engine_info is not None and engine_wall_time_ms is not None:
                    engine_info.set_extra("engine_wall_time_ms", int(engine_wall_time_ms))
        return record
    except ValueError as exc:
        raise RuntimeError(f"Illegal move in final payload: {exc}") from exc


__all__ = ["build_remote_game_info"]
