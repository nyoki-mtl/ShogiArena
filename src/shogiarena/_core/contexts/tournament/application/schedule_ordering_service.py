"""Schedule ordering helpers for tournament game specs."""

from __future__ import annotations

import random
from collections import defaultdict

from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


def reorder_and_shuffle(
    games: list[GameSpec],
    *,
    game_order: str,
    flip_policy: str,
    shuffle_seed: int | float | str | bytes | bytearray | None,
) -> list[GameSpec]:
    """スケジュール順序ポリシーを適用する。"""

    order = _resolve_game_order(game_order=game_order, flip_policy=flip_policy)
    if order == "interleave":
        ordered = _interleave_pairs(games)
    else:
        ordered = list(games)

    if order == "shuffle":
        rng = random.Random(shuffle_seed)
        rng.shuffle(ordered)
    return ordered


def _resolve_game_order(*, game_order: str, flip_policy: str) -> str:
    if game_order != "auto":
        return game_order
    return "pairwise" if flip_policy == "pair_both" else "interleave"


def _interleave_pairs(games: list[GameSpec]) -> list[GameSpec]:
    buckets: dict[tuple[str, str], list[GameSpec]] = defaultdict(list)
    for spec in games:
        black_name = spec.black_engine
        white_name = spec.white_engine
        pair_key = (black_name, white_name) if black_name <= white_name else (white_name, black_name)
        buckets[pair_key].append(spec)

    pair_keys = sorted(buckets.keys())
    ordered: list[GameSpec] = []
    max_len = max((len(items) for items in buckets.values()), default=0)
    for idx in range(max_len):
        for pair_key in pair_keys:
            pair_games = buckets[pair_key]
            if idx < len(pair_games):
                ordered.append(pair_games[idx])
    return ordered


__all__ = ["reorder_and_shuffle"]
