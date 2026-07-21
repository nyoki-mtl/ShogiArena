"""Pentanomial statistics computation for paired games."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TypedDict

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_str
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    is_decisive_result,
    round_index_from_game_name,
    tested_score,
)


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

    groups: dict[tuple[str, str, str, int], list[GameRecordPlayers]] = defaultdict(list)
    for game in games:
        black = coerce_str(game.get("black_player")) or ""
        white = coerce_str(game.get("white_player")) or ""
        if not black or not white:
            continue
        result = coerce_game_result(game.get("result"))
        if result is None or not is_decisive_result(result):
            continue
        round_idx = round_index_from_game_name(coerce_str(game.get("game_name")) or "")
        if round_idx is None:
            continue
        pair_key = _normalize_pair(black, white)
        sfen = coerce_str(game.get("initial_sfen")) or "startpos"
        groups[(pair_key[0], pair_key[1], sfen, round_idx // 2)].append(game)

    bins = {"2.0": 0, "1.5": 0, "1.0": 0, "0.5": 0, "0.0": 0}
    pairs_used = 0

    for (engine_1, _engine_2, _sfen, _pair_slot), records in groups.items():
        engine_1_as_black = [g for g in records if (coerce_str(g.get("black_player")) or "") == engine_1]
        engine_1_as_white = [g for g in records if (coerce_str(g.get("white_player")) or "") == engine_1]
        if not engine_1_as_black or not engine_1_as_white:
            continue

        pair_count = min(len(engine_1_as_black), len(engine_1_as_white))
        for idx in range(pair_count):
            game_black = engine_1_as_black[idx]
            game_white = engine_1_as_white[idx]
            result_black = coerce_game_result(game_black.get("result"))
            result_white = coerce_game_result(game_white.get("result"))
            if result_black is None or result_white is None:
                continue
            score = tested_score(result_black, is_tested_black=True) + tested_score(
                result_white,
                is_tested_black=False,
            )

            bucket = f"{score:.1f}"
            if bucket in bins:
                bins[bucket] += 1
                pairs_used += 1

    return {"pairs": pairs_used, "bins": bins}


__all__ = ["compute_pentanomial"]
