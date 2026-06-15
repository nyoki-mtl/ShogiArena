"""Tested-engine-perspective pentanomial pairing helpers.

A pentanomial sample combines two games -- the tested engine playing black and white on the same
opening -- into a single paired outcome. These helpers are shared by the OpenBench totals, the
dashboard SPRT replay, and the SPRT pair buffer so they all agree on which bin a pair lands in and
always score from the *tested* engine's perspective (never the dictionary-order engine).

Bins are returned in score-ascending order: index 0 = LL, 1 = LD+DL, 2 = LW+DD+WL, 3 = DW+WD,
4 = WW, matching the GSPRT 5-bin histogram input.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str

PENTANOMIAL_BIN_COUNT = 5


def is_decisive_result(result: GameResult) -> bool:
    """True for a played game with a W/D/L outcome.

    Excludes non-game outcomes (``ERROR`` / ``INVALID`` / ``PAUSED``). Use this for aggregation
    paths (OpenBench totals, pentanomial bins) that must not fold a non-decided game into a draw.
    Unlike :func:`should_sample_for_sprt`, this never raises; the caller decides what to do with a
    non-decided game (e.g. count it as a crash, or skip it).
    """
    return result.is_draw() or result.is_black_win() or result.is_white_win()


def should_sample_for_sprt(result: GameResult, *, context: str) -> bool:
    """Decide whether a game result is a valid SPRT observation.

    This is the single source of truth for how non-decisive outcomes are handled across every
    SPRT feed (live arena, LTC regression, dashboard replay):

    - Decisive results and genuine draws are valid observations: returns ``True`` (feed the sample).
    - ``PAUSED`` is a normal interruption path (user stop / shutdown), not a played game: returns
      ``False`` so the caller excludes it from the sample rather than counting it as a draw.
    - ``ERROR`` / ``INVALID`` / any other non-game outcome indicates an upstream bug and must
      surface rather than silently distort the test: raises ``ValueError``.

    Args:
        result: The game result, already normalized to the tested engine's perspective if needed.
        context: Short human-readable locator (e.g. ``"game g0007-x"``) included in the error.
    """
    if result == GameResult.PAUSED:
        return False
    if is_decisive_result(result):
        return True
    raise ValueError(f"SPRT cannot ingest non-decisive game result {game_result_name(result)} ({context})")


def round_index_from_game_name(game_name: str) -> int | None:
    """Recover a 0-based round index from a ``g0001-...`` game name (DB/replay fallback)."""
    match = re.match(r"^g(?P<round>\d+)-", game_name)
    if match is None:
        return None
    one_based = coerce_int(match.group("round"))
    if one_based is None or one_based <= 0:
        return None
    return one_based - 1


def tested_score(result: GameResult, *, is_tested_black: bool) -> float:
    """Per-game score (0.0 loss / 0.5 draw / 1.0 win) from the tested engine's perspective."""
    if result.is_draw():
        return 0.5
    if is_tested_black:
        if result.is_black_win():
            return 1.0
        if result.is_white_win():
            return 0.0
    else:
        if result.is_white_win():
            return 1.0
        if result.is_black_win():
            return 0.0
    return 0.5


def pair_score_bin_index(pair_score: float) -> int:
    """Map a pair score in {0, 0.5, 1, 1.5, 2} to its pentanomial bin index (0=LL .. 4=WW)."""
    if pair_score >= 1.99:
        return 4
    if pair_score >= 1.49:
        return 3
    if pair_score >= 0.99:
        return 2
    if pair_score >= 0.49:
        return 1
    return 0


def compute_pentanomial_bins(games: Iterable[GameRecordPlayers], *, tested_engine: str, base_engine: str) -> list[int]:
    """Compute the 5 pentanomial bins (score-ascending) for the tested-vs-base matchup.

    Only games of the exact ``{tested_engine, base_engine}`` 1v1 matchup are considered, so a list
    that mixes opponents (e.g. a gauntlet or round-robin) cannot zip a ``tested vs A`` game with a
    ``tested vs B`` game into a fictitious pair. Games are grouped deterministically by
    ``(opening sfen, pair slot)`` using the round token in the game name (two rounds per opening
    pair); within a group the tested engine's black and white games are zipped in arrival order.
    Games of another matchup, or without a recoverable round token, are ignored.
    """
    matchup = {tested_engine, base_engine}
    groups: dict[tuple[str, int], list[GameRecordPlayers]] = defaultdict(list)
    for game in games:
        black = coerce_str(game.get("black_player")) or ""
        white = coerce_str(game.get("white_player")) or ""
        if {black, white} != matchup:
            continue
        # Non-decided games (crash/paused/invalid) are not valid pentanomial observations; dropping
        # them here means an incomplete pair never forms a fictitious draw-draw (DD) bin.
        if not is_decisive_result(game["result"]):
            continue
        sfen = coerce_str(game.get("initial_sfen")) or "startpos"
        game_name = coerce_str(game.get("game_name")) or ""
        round_idx = round_index_from_game_name(game_name)
        if round_idx is None:
            continue
        groups[(sfen, round_idx // 2)].append(game)

    bins = [0] * PENTANOMIAL_BIN_COUNT
    for group in groups.values():
        tested_black = [g for g in group if (coerce_str(g.get("black_player")) or "") == tested_engine]
        tested_white = [g for g in group if (coerce_str(g.get("white_player")) or "") == tested_engine]
        for game_black, game_white in zip(tested_black, tested_white, strict=False):
            score = tested_score(game_black["result"], is_tested_black=True) + tested_score(
                game_white["result"], is_tested_black=False
            )
            bins[pair_score_bin_index(score)] += 1
    return bins


__all__ = [
    "PENTANOMIAL_BIN_COUNT",
    "compute_pentanomial_bins",
    "is_decisive_result",
    "pair_score_bin_index",
    "round_index_from_game_name",
    "should_sample_for_sprt",
    "tested_score",
]
