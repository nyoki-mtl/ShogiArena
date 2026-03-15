from __future__ import annotations

import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena._core.shared.kernel.game_results import GameResult


def _make_record(
    black_tc: str | None,
    white_tc: str | None,
    *,
    result_code: GameResult = GameResult.PAUSED,
) -> object:
    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "black_time_control": black_tc,
                "white_time_control": white_tc,
                "attributes": {"game_name": "g", "game_type": "arena"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result_code.name, "ply_count": 0},
        }
    )


def test_to_kif_uses_rshogi_serializer() -> None:
    record = _make_record("900+60+0", "900+60+0")
    assert isinstance(record.to_kif(), str)


def test_to_kif_increment_time_controls() -> None:
    record = _make_record("900+0+5", "900+0+5")
    kif = record.to_kif()
    assert "先手持ち時間：900+0+5" in kif
    assert "後手持ち時間：900+0+5" in kif


def test_to_kif_side_time_controls() -> None:
    record = _make_record("600+0+10", "900+0+10")
    kif = record.to_kif()
    assert "先手持ち時間：600+0+10" in kif
    assert "後手持ち時間：900+0+10" in kif


def test_to_csa_uses_rshogi_serializer() -> None:
    record = _make_record("1500+60+0", "1500+60+0")
    assert isinstance(record.to_csa(), str)


def test_to_csa_emits_side_time_limits_for_common_spec() -> None:
    record = _make_record("1500+60+0", "1500+60+0")
    csa = record.to_csa()
    assert "$TIME+:1500+60+0" in csa
    assert "$TIME-:1500+60+0" in csa
    assert "$TIME_LIMIT:" not in csa


def test_to_csa_time_limit_v30() -> None:
    record = _make_record("900+0+5", "900+0+5")
    csa = record.to_csa()
    assert "$TIME+:900+0+5" in csa
    assert "$TIME-:900+0+5" in csa
    assert "$TIME_LIMIT:" not in csa


def test_to_csa_draw_markers_follow_game_result_codes() -> None:
    record_max = _make_record("900+0+0", "900+0+0", result_code=GameResult.DRAW_BY_MAX_PLIES)
    record_impasse = _make_record("900+0+0", "900+0+0", result_code=GameResult.DRAW_BY_IMPASSE)

    csa_max = record_max.to_csa()
    csa_impasse = record_impasse.to_csa()

    assert csa_max.rstrip().endswith("%MAX_MOVES")
    assert csa_impasse.rstrip().endswith("%JISHOGI")
