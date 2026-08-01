"""SPSA optimizerへ渡す対局結果の分類。"""

from __future__ import annotations

from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name


class SpsaObservationError(RuntimeError):
    """Optimizer updateへ使用できないfailed observation。"""


class SpsaIncompleteObservationError(SpsaObservationError):
    """停止または欠落により完了していないobservation。"""


def winner_code_from_result(result: GameResult | None, *, is_tuned_as_black: bool) -> int:
    """ValidなWDLだけをoptimizer wire codeへ変換する。"""
    if result is None:
        raise SpsaIncompleteObservationError("SPSA observation result is missing")
    if result.is_black_win():
        return 1 if is_tuned_as_black else 0
    if result.is_white_win():
        return 0 if is_tuned_as_black else 1
    if result.is_draw():
        return 2
    if result == GameResult.PAUSED:
        raise SpsaIncompleteObservationError("SPSA observation is incomplete: PAUSED")
    raise SpsaObservationError(f"SPSA observation failed: {game_result_name(result)}")


__all__ = [
    "SpsaIncompleteObservationError",
    "SpsaObservationError",
    "winner_code_from_result",
]
