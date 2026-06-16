from __future__ import annotations

import pytest

from shogiarena._core.platform.engine_provisioning.remote_game_record_builder import build_remote_game_info
from shogiarena._core.shared.kernel.game_results import STARTING_SFEN, GameResult
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def test_build_remote_game_info_uses_legal_usi_moves() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)

    record = build_remote_game_info(
        start_sfen="startpos",
        moves=("7g7f", "3c3d"),
        game_result=GameResult.DRAW_BY_REPETITION,
        game_id="remote-1",
        black_name="black",
        white_name="white",
        black_limits=limits,
        white_limits=limits,
    )

    assert record.init_position_sfen == STARTING_SFEN
    assert tuple(move_record.move.to_usi() for move_record in record.moves) == ("7g7f", "3c3d")
    assert record.result == GameResult.DRAW_BY_REPETITION


def test_build_remote_game_info_attaches_engine_wall_time_extras() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)

    record = build_remote_game_info(
        start_sfen="startpos",
        moves=("7g7f",),
        game_result=GameResult.BLACK_WIN,
        game_id="remote-engine-wall",
        black_name="black",
        white_name="white",
        black_limits=limits,
        white_limits=limits,
        wall_times=(21,),
        engine_wall_times=(17,),
    )

    assert record.moves[0].engine_info.extras["engine_wall_time_ms"] == 17


def test_build_remote_game_info_rejects_illegal_move() -> None:
    limits = TimeControlLimits(time_ms=1000, increment_ms=0)

    with pytest.raises(RuntimeError, match="Illegal move in final payload"):
        build_remote_game_info(
            start_sfen="startpos",
            moves=("7g7e",),
            game_result=GameResult.BLACK_WIN,
            game_id="remote-illegal",
            black_name="black",
            white_name="white",
            black_limits=limits,
            white_limits=limits,
        )
