"""完了局を SPRT の観測へ正規化する（task 0052 / review H2）。

対局の完了時（live）と resume 時の replay で、同じ局が同じ観測にならなければ、
resume した run は別の統計の下で継続してしまう。正規化を 1 箇所に置いて分岐させない。
"""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import tested_score


@dataclass(frozen=True, slots=True)
class SprtObservation:
    """tested engine 視点へ正規化した 1 局。"""

    is_tested_black: bool

    trinomial_result: GameResult
    """trinomial model が受け取る結果。tested の勝ちを ``WHITE_WIN`` として表す。"""

    tested_score: float
    """pentanomial model が受け取る素点（0.0 / 0.5 / 1.0）。"""


def normalize_sprt_observation(result: GameResult, *, is_tested_black: bool) -> SprtObservation:
    """盤面視点の結果を tested engine 視点へ写す。

    Args:
        result: 盤面視点の結果（先手勝ち / 後手勝ち / 引き分け）。
        is_tested_black: tested engine が先手だったか。

    Returns:
        正規化した観測。
    """
    if is_tested_black:
        trinomial = (
            GameResult.WHITE_WIN
            if result.is_black_win()
            else GameResult.BLACK_WIN
            if result.is_white_win()
            else GameResult.DRAW_BY_MAX_PLIES
        )
    else:
        trinomial = (
            GameResult.WHITE_WIN
            if result.is_white_win()
            else GameResult.BLACK_WIN
            if result.is_black_win()
            else GameResult.DRAW_BY_MAX_PLIES
        )
    return SprtObservation(
        is_tested_black=is_tested_black,
        trinomial_result=trinomial,
        tested_score=tested_score(result, is_tested_black=is_tested_black),
    )


__all__ = ["SprtObservation", "normalize_sprt_observation"]
