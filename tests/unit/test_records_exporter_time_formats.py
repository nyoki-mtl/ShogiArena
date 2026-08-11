from __future__ import annotations

import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.platform.records.codecs import get_reader, get_serializer
from shogiarena._core.shared.kernel.game_results import GameResult


def _make_record(
    black_tc: str | None,
    white_tc: str | None,
    *,
    result_code: GameResult = GameResult.PAUSED,
) -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
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


def _make_evaluated_record() -> rsshogi.record.Record:
    return rsshogi.record.Record.from_usi_main_line(
        InitialPosition.STANDARD.value,
        ["7g7f", "3c3d"],
        result=GameResult.PAUSED,
        evals=[100, 120],
    )


def _evals(record: rsshogi.record.Record) -> tuple[int | None, ...]:
    moves = record.to_dict().get("moves", [])
    assert isinstance(moves, list)
    evals: list[int | None] = []
    for move in moves:
        engine_info = move.get("engine_info") if isinstance(move, dict) else None
        value = engine_info.get("eval") if isinstance(engine_info, dict) else None
        evals.append(value if isinstance(value, int) else None)
    return tuple(evals)


def test_to_kif_uses_rsshogi_serializer() -> None:
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


def test_kif_converts_side_to_move_evals_to_black_perspective_and_back() -> None:
    serializer = get_serializer("kif")
    reader = get_reader("kif")
    assert serializer is not None
    assert reader is not None

    payload = serializer.serialize(_make_evaluated_record())
    assert isinstance(payload, str)
    assert "**評価値=100" in payload
    assert "**評価値=-120" in payload

    restored = reader.deserialize(payload)
    assert _evals(restored) == (100, 120)


def test_to_csa_uses_rsshogi_serializer() -> None:
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


def test_csa_preserves_side_to_move_eval_perspective() -> None:
    serializer = get_serializer("csa")
    reader = get_reader("csa")
    assert serializer is not None
    assert reader is not None

    payload = serializer.serialize(_make_evaluated_record())
    assert isinstance(payload, str)
    assert "'** 100" in payload
    assert "'** 120" in payload

    restored = reader.deserialize(payload)
    assert _evals(restored) == (100, 120)


def test_to_csa_draw_markers_follow_game_result_codes() -> None:
    record_max = _make_record("900+0+0", "900+0+0", result_code=GameResult.DRAW_BY_MAX_PLIES)
    record_impasse = _make_record("900+0+0", "900+0+0", result_code=GameResult.DRAW_BY_IMPASSE)

    csa_max = record_max.to_csa()
    csa_impasse = record_impasse.to_csa()

    assert csa_max.rstrip().endswith("%MAX_MOVES")
    assert csa_impasse.rstrip().endswith("%JISHOGI")
