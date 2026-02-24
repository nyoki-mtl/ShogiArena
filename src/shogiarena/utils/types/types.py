"""rshogi ベースの型/結果ユーティリティ。"""

from __future__ import annotations

from typing import TypedDict

from rshogi.initial_positions import InitialPosition
from rshogi.record import GameResult
from rshogi.types import Color

STARTING_SFEN: str = InitialPosition.STANDARD.value
"""平手初期局面の SFEN 文字列。"""

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


class GameRecordDict(TypedDict):
    black_player: str
    white_player: str
    result: GameResult


class GameRecordPlayersDict(GameRecordDict, total=False):
    game_id: int
    game_name: str
    initial_sfen: str | None


class GameRecordEnginesDict(TypedDict):
    black_engine: str
    white_engine: str
    result: GameResult
    game_name: str
    initial_sfen: str | None


EngineOptionsSnapshots = dict[str, dict[str, JsonValue]]
EngineInfoSnapshots = dict[str, dict[str, str]]

__all__ = [
    "Color",
    "GameResult",
    "GameRecordDict",
    "GameRecordPlayersDict",
    "GameRecordEnginesDict",
    "JsonScalar",
    "JsonValue",
    "JsonObject",
    "EngineOptionsSnapshots",
    "EngineInfoSnapshots",
    "STARTING_SFEN",
    "game_result_score",
    "game_result_terminal_kind",
    "timeout_win_result",
]

_GAME_RESULT_TO_TERMINAL_KIND: dict[int, str] = {
    GameResult.BLACK_WIN.value: "RESIGN",
    GameResult.WHITE_WIN.value: "RESIGN",
    GameResult.DRAW_BY_REPETITION.value: "REPETITION_DRAW",
    GameResult.ERROR.value: "INTERRUPT",
    GameResult.BLACK_WIN_BY_DECLARATION.value: "ENTERING_OF_KING",
    GameResult.WHITE_WIN_BY_DECLARATION.value: "ENTERING_OF_KING",
    GameResult.DRAW_BY_MAX_PLIES.value: "MAX_MOVES",
    GameResult.BLACK_WIN_BY_FORFEIT.value: "WIN_BY_DEFAULT",
    GameResult.WHITE_WIN_BY_FORFEIT.value: "WIN_BY_DEFAULT",
    GameResult.DRAW_BY_IMPASSE.value: "IMPASSE",
    GameResult.INVALID.value: "INTERRUPT",
    GameResult.BLACK_WIN_BY_ILLEGAL_MOVE.value: "FOUL_WIN",
    GameResult.WHITE_WIN_BY_ILLEGAL_MOVE.value: "FOUL_WIN",
    GameResult.BLACK_WIN_BY_TIMEOUT.value: "TIMEOUT",
    GameResult.WHITE_WIN_BY_TIMEOUT.value: "TIMEOUT",
    GameResult.PAUSED.value: "INTERRUPT",
}


def timeout_win_result(winner: Color) -> GameResult:
    """勝者色から時間切れ勝ち結果を返す。"""
    return GameResult.BLACK_WIN_BY_TIMEOUT if winner == Color.BLACK else GameResult.WHITE_WIN_BY_TIMEOUT


def game_result_terminal_kind(result: GameResult) -> str:
    """GameResult から rshogi SpecialMoveRecord の kind 文字列を返す。"""
    return _GAME_RESULT_TO_TERMINAL_KIND.get(result.value, "UNKNOWN")


def game_result_score(result: GameResult, perspective: Color) -> float | None:
    """指定視点からのスコアを返す (win=1, draw=0.5, lose=0, invalid=None)。"""
    if result.is_black_win():
        return 1.0 if perspective == Color.BLACK else 0.0
    if result.is_white_win():
        return 1.0 if perspective == Color.WHITE else 0.0
    if result.is_draw():
        return 0.5
    return None
