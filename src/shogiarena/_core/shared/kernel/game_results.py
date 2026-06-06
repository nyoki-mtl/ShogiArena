"""Game-result constants and helpers shared across contexts."""

from __future__ import annotations

from rshogi.initial_positions import InitialPosition
from rshogi.record import GameResult
from rshogi.types import Color

STARTING_SFEN: str = InitialPosition.STANDARD.value


def timeout_win_result(winner: Color) -> GameResult:
    return GameResult.BLACK_WIN_BY_TIMEOUT if winner == Color.BLACK else GameResult.WHITE_WIN_BY_TIMEOUT


def game_result_name(result: GameResult) -> str:
    return result.name


def parse_game_result_name(raw: str) -> GameResult:
    normalized = raw.strip()
    if not normalized:
        raise ValueError("game_result must be a non-empty enum name")
    try:
        return GameResult.from_str(normalized)
    except ValueError as exc:
        raise ValueError(f"unknown GameResult name: {raw!r}") from exc


__all__ = [
    "GameResult",
    "STARTING_SFEN",
    "game_result_name",
    "parse_game_result_name",
    "timeout_win_result",
]
