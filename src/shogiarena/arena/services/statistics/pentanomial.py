"""
Pentanomial statistics computation for paired games.

We group games by (unordered engine pair, initial_sfen). For each group, we
seek two complementary games with swapped colors and compute the 2-game score
for the lexicographically first engine. The score per pair falls into one of
five bins: 2.0, 1.5, 1.0, 0.5, 0.0.

This is a best-effort aggregation from the DB; incomplete pairs are ignored.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TypedDict

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.utils.types.coerce import coerce_game_result
from shogiarena.utils.types.types import GameRecordPlayersDict


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    a = str(a)
    b = str(b)
    return (a, b) if a <= b else (b, a)


class PentanomialResult(TypedDict):
    pairs: int
    bins: dict[str, int]


def compute_pentanomial(db: ArenaDBService) -> PentanomialResult:
    """Compute pentanomial distribution from stored games.

    Returns a dict with keys:
      - pairs: number of paired (2-game) samples used
      - bins: {"2.0": int, "1.5": int, "1.0": int, "0.5": int, "0.0": int}
    """
    games = db.get_games_with_players()
    # Group by (pair_key, sfen)
    groups: dict[tuple[str, str, str], list[GameRecordPlayersDict]] = defaultdict(list)
    for g in games:
        a = str(g["black_player"])
        b = str(g["white_player"])
        if not a or not b:
            continue
        key = _normalize_pair(a, b)
        sfen = str(g.get("initial_sfen") or "startpos")
        groups[(key[0], key[1], sfen)].append(g)

    bins = {"2.0": 0, "1.5": 0, "1.0": 0, "0.5": 0, "0.0": 0}
    pairs_used = 0

    for (e1, _e2, _sfen), lst in groups.items():
        # Partition by color orientation
        g_e1_black = [g for g in lst if str(g.get("black_player")) == e1]
        g_e1_white = [g for g in lst if str(g.get("white_player")) == e1]
        if not g_e1_black or not g_e1_white:
            # Need at least one of each color to form a pair
            continue

        # Pair them by index (best-effort)
        count = min(len(g_e1_black), len(g_e1_white))
        for i in range(count):
            g1 = g_e1_black[i]
            g2 = g_e1_white[i]
            score = 0.0

            # g1: e1 is black
            gr1 = coerce_game_result(g1.get("result"))
            if gr1 is not None:
                if gr1.is_black_win():
                    score += 1.0
                elif gr1.is_draw():
                    score += 0.5
            # else loss => +0

            # g2: e1 is white
            gr2 = coerce_game_result(g2.get("result"))
            if gr2 is not None:
                if gr2.is_white_win():
                    score += 1.0
                elif gr2.is_draw():
                    score += 0.5

            key_bin = f"{score:.1f}"
            if key_bin in bins:
                bins[key_bin] += 1
                pairs_used += 1

    return {"pairs": pairs_used, "bins": bins}
