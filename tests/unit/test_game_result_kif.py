import rsshogi

from shogiarena._core.shared.kernel.game_record_types import Color, game_result_score
from shogiarena._core.shared.kernel.game_results import GameResult, timeout_win_result


def test_game_result_from_kif_resign() -> None:
    kif = "\n".join(
        [
            "手合割：平手",
            "先手：Black",
            "後手：White",
            "手数----指手---------消費時間--",
            "1 投了",
            "まで1手で後手の勝ち",
        ]
    )
    assert rsshogi.record.Record.from_kif_str(kif).result == GameResult.WHITE_WIN


def test_game_result_from_kif_chudan() -> None:
    kif = "\n".join(
        [
            "手合割：平手",
            "先手：Black",
            "後手：White",
            "手数----指手---------消費時間--",
            "1 中断",
            "まで1手で中断",
        ]
    )
    assert rsshogi.record.Record.from_kif_str(kif).result == GameResult.PAUSED


def test_game_result_from_kif_max_plies() -> None:
    kif = "\n".join(
        [
            "手合割：平手",
            "先手：Black",
            "後手：White",
            "手数----指手---------消費時間--",
            "1 最大手数",
            "まで1手で最大手数",
        ]
    )
    assert rsshogi.record.Record.from_kif_str(kif).result == GameResult.DRAW_BY_MAX_PLIES


def test_game_result_from_kif_jishogi() -> None:
    kif = "\n".join(
        [
            "手合割：平手",
            "先手：Black",
            "後手：White",
            "手数----指手---------消費時間--",
            "1 持将棋",
            "まで1手で持将棋",
        ]
    )
    assert rsshogi.record.Record.from_kif_str(kif).result == GameResult.DRAW_BY_IMPASSE


def test_game_result_score_and_timeout_helpers() -> None:
    assert game_result_score(GameResult.BLACK_WIN, Color.BLACK) == 1.0
    assert game_result_score(GameResult.BLACK_WIN, Color.WHITE) == 0.0
    assert game_result_score(GameResult.DRAW_BY_REPETITION, Color.BLACK) == 0.5
    assert game_result_score(GameResult.PAUSED, Color.BLACK) is None
    assert timeout_win_result(Color.BLACK) == GameResult.BLACK_WIN_BY_TIMEOUT
    assert timeout_win_result(Color.WHITE) == GameResult.WHITE_WIN_BY_TIMEOUT
