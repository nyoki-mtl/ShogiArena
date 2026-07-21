from __future__ import annotations

import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.shared.kernel.game_results import GameResult


def _record_with_result(result: GameResult) -> object:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "g", "game_type": "arena"},
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result.name, "ply_count": 0},
        }
    )


def test_to_kif_normalizes_error_result_to_interrupt_label() -> None:
    kif = _record_with_result(GameResult.ERROR).to_kif()
    assert "中断" in kif


def test_to_kif_draw_labels_follow_game_result_codes() -> None:
    kif_max = _record_with_result(GameResult.DRAW_BY_MAX_PLIES).to_kif()
    kif_impasse = _record_with_result(GameResult.DRAW_BY_IMPASSE).to_kif()
    kif_paused = _record_with_result(GameResult.PAUSED).to_kif()

    assert "最大手数" in kif_max
    assert "持将棋" in kif_impasse
    assert "中断" in kif_paused
