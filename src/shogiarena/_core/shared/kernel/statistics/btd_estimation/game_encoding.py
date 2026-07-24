from __future__ import annotations

from collections.abc import Iterable

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers

from .constants import GameResultInput

EncodedGame = tuple[str, str, int, int, int]


def collect_engines(games: Iterable[GameRecordPlayers]) -> set[str]:
    names: set[str] = set()
    for game in games:
        black_player = game.get("black_player")
        white_player = game.get("white_player")
        if black_player:
            names.add(str(black_player))
        if white_player:
            names.add(str(white_player))
    return names


def _encode_result(raw: GameResultInput) -> tuple[int, int, int]:
    if raw is None:
        return (0, 0, 0)
    game_result = coerce_game_result(raw)
    if game_result is None:
        if isinstance(raw, str):
            normalized = raw.strip().upper()
            return (
                1 if "BLACK" in normalized else 0,
                1 if "WHITE" in normalized else 0,
                1 if "DRAW" in normalized else 0,
            )
        return (0, 0, 0)
    return (
        1 if game_result.is_black_win() else 0,
        1 if game_result.is_white_win() else 0,
        1 if game_result.is_draw() else 0,
    )


def encode_games(records: Iterable[GameRecordPlayers]) -> list[EncodedGame]:
    encoded_games: list[EncodedGame] = []
    for record in records:
        black_player = str(record.get("black_player", ""))
        white_player = str(record.get("white_player", ""))
        if not black_player or not white_player:
            continue
        raw_result = record.get("result")
        if raw_result is not None and not isinstance(raw_result, str | int | GameResult):
            continue
        black_wins, white_wins, draws = _encode_result(raw_result)
        if (black_wins + white_wins + draws) == 0:
            continue
        encoded_games.append((black_player, white_player, black_wins, white_wins, draws))
    return encoded_games


def aggregate_encoded_games(encoded_games: Iterable[EncodedGame]) -> list[EncodedGame]:
    """Collapse per-game rows into one row per ordered ``(black, white)`` pair.

    Every term of the BTD log-likelihood and its gradient depends on a game only through its
    ordered pair, so summing the win/draw counts per pair is exact rather than an approximation.
    It makes the optimizer's cost depend on the number of distinct pairings instead of the number
    of games played, which otherwise grows without bound over a long run.
    """

    totals: dict[tuple[str, str], list[int]] = {}
    for black_player, white_player, black_wins, white_wins, draws in encoded_games:
        counts = totals.get((black_player, white_player))
        if counts is None:
            totals[(black_player, white_player)] = [black_wins, white_wins, draws]
            continue
        counts[0] += black_wins
        counts[1] += white_wins
        counts[2] += draws
    return [(black, white, counts[0], counts[1], counts[2]) for (black, white), counts in totals.items()]


__all__ = ["EncodedGame", "aggregate_encoded_games", "collect_engines", "encode_games"]
