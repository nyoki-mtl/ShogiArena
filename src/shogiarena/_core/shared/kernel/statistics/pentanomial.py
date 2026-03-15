"""Pentanomial statistics computation for paired games."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TypedDict

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers


class PentanomialResult(TypedDict):
    pairs: int
    bins: dict[str, int]


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    left = str(a)
    right = str(b)
    return (left, right) if left <= right else (right, left)


def compute_pentanomial(games: Iterable[GameRecordPlayers]) -> PentanomialResult:
    """Compute pentanomial distribution from a game-record iterable.

    Returns:
      - ``pairs``: number of paired (2-game) samples used
      - ``bins``: ``{"2.0", "1.5", "1.0", "0.5", "0.0"}`` histogram
    """

    groups: dict[tuple[str, str, str], list[GameRecordPlayers]] = defaultdict(list)
    for game in games:
        black = str(game.get("black_player") or "")
        white = str(game.get("white_player") or "")
        if not black or not white:
            continue
        pair_key = _normalize_pair(black, white)
        sfen = str(game.get("initial_sfen") or "startpos")
        groups[(pair_key[0], pair_key[1], sfen)].append(game)

    bins = {"2.0": 0, "1.5": 0, "1.0": 0, "0.5": 0, "0.0": 0}
    pairs_used = 0

    for (engine_1, _engine_2, _sfen), records in groups.items():
        engine_1_as_black = [g for g in records if str(g.get("black_player")) == engine_1]
        engine_1_as_white = [g for g in records if str(g.get("white_player")) == engine_1]
        if not engine_1_as_black or not engine_1_as_white:
            continue

        pair_count = min(len(engine_1_as_black), len(engine_1_as_white))
        for idx in range(pair_count):
            game_black = engine_1_as_black[idx]
            game_white = engine_1_as_white[idx]
            score = 0.0

            result_black = coerce_game_result(game_black.get("result"))
            if result_black is not None:
                if result_black.is_black_win():
                    score += 1.0
                elif result_black.is_draw():
                    score += 0.5

            result_white = coerce_game_result(game_white.get("result"))
            if result_white is not None:
                if result_white.is_white_win():
                    score += 1.0
                elif result_white.is_draw():
                    score += 0.5

            bucket = f"{score:.1f}"
            if bucket in bins:
                bins[bucket] += 1
                pairs_used += 1

    return {"pairs": pairs_used, "bins": bins}


__all__ = ["compute_pentanomial"]
