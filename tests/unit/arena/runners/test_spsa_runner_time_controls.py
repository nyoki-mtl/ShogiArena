from __future__ import annotations

import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena.utils.types.types import GameResult


def _make_game_record(*, black_tc: str | None = None, white_tc: str | None = None) -> rshogi.record.GameRecord:
    metadata: dict[str, object] = {
        "black_player": "black",
        "white_player": "white",
        "attributes": {
            "game_name": "game-001",
            "game_type": "spsa",
            "updated_date": "2026-01-01T00:00:00",
        },
    }
    if black_tc is not None:
        metadata["black_time_control"] = black_tc
    if white_tc is not None:
        metadata["white_time_control"] = white_tc
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": metadata,
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": GameResult.DRAW_BY_REPETITION.name, "ply_count": 0},
        },
        strict=True,
    )


def test_record_time_controls_handles_missing_time_controls() -> None:
    record = _make_game_record()

    assert record.black_time_control is None
    assert record.white_time_control is None


def test_record_time_controls_reads_side_specific_specs() -> None:
    record = _make_game_record(black_tc="60+1+0", white_tc="90+0+0")
    assert record.black_time_control is not None
    assert record.white_time_control is not None
    assert record.black_time_control.to_spec() == "60+1+0"
    assert record.white_time_control.to_spec() == "90+0+0"
