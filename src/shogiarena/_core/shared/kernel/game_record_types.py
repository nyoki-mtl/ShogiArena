"""Game result scoring helpers."""

from __future__ import annotations

from typing import TypedDict

from rshogi.types import Color

from shogiarena._core.shared.kernel.game_results import GameResult


class GameRecordEnginesDict(TypedDict):
    black_engine: str
    white_engine: str
    result: GameResult
    game_name: str
    initial_sfen: str | None


def game_result_score(result: GameResult, perspective: Color) -> float | None:
    """Return score from perspective (win=1, draw=0.5, loss=0, invalid=None)."""

    if result.is_black_win():
        return 1.0 if perspective == Color.BLACK else 0.0
    if result.is_white_win():
        return 1.0 if perspective == Color.WHITE else 0.0
    if result.is_draw():
        return 0.5
    return None


__all__ = [
    "Color",
    "GameRecordEnginesDict",
    "game_result_score",
]
