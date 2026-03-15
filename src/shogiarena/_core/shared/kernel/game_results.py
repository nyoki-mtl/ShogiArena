"""Game-result constants and helpers shared across contexts."""

from __future__ import annotations

from rshogi.initial_positions import InitialPosition
from rshogi.record import GameResult
from rshogi.types import Color

STARTING_SFEN: str = InitialPosition.STANDARD.value

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
    return GameResult.BLACK_WIN_BY_TIMEOUT if winner == Color.BLACK else GameResult.WHITE_WIN_BY_TIMEOUT


def game_result_terminal_kind(result: GameResult) -> str:
    return _GAME_RESULT_TO_TERMINAL_KIND.get(result.value, "UNKNOWN")


def game_result_name(result: GameResult) -> str:
    return result.name


def parse_game_result_name(raw: str) -> GameResult:
    normalized = raw.strip()
    if not normalized:
        raise ValueError("game_result must be a non-empty enum name")
    member = GameResult.__members__.get(normalized)
    if member is None:
        raise ValueError(f"unknown GameResult name: {raw!r}")
    return member


__all__ = [
    "GameResult",
    "STARTING_SFEN",
    "game_result_terminal_kind",
    "game_result_name",
    "parse_game_result_name",
    "timeout_win_result",
]
