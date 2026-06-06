from __future__ import annotations

import rshogi

from shogiarena._core.shared.kernel.game_results import GameResult


def test_csa_parser_extracts_terminal_time_and_comment() -> None:
    csa = "V2.2\nN+Black\nN-White\nPI\n+\n+7776FU,T1\n-3334FU,T2\n%TORYO,T3,'*terminal comment\n"

    record = rshogi.record.GameRecord.from_csa_str(csa)
    payload = record.to_dict()
    result = payload.get("result")
    assert isinstance(result, dict)

    assert int(record.result) == int(GameResult.WHITE_WIN)
    assert result.get("ply_count") == 2
    assert result.get("end_time_ms") == 3000
    assert result.get("end_comment") == "terminal comment"


def test_kif_parser_extracts_terminal_time() -> None:
    kif = (
        "手合割：平手\n"
        "先手：先手\n"
        "後手：後手\n"
        "手数----指手---------消費時間--\n"
        "   1 ７六歩(77)\n"
        "   2 ３四歩(33)\n"
        "   3 投了      ( 0:03/00:00:04)\n"
        "まで2手で後手の勝ち\n"
    )

    record = rshogi.record.GameRecord.from_kif_str(kif)
    payload = record.to_dict()
    result = payload.get("result")
    assert isinstance(result, dict)

    assert int(record.result) == int(GameResult.WHITE_WIN)
    assert result.get("ply_count") == 2
    assert result.get("end_time_ms") == 3000
    assert result.get("end_comment") is None
