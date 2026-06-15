"""OpenBench result aggregation helpers."""

from __future__ import annotations

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort, GameRecordPlayers
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    compute_pentanomial_bins,
    is_decisive_result,
    tested_score,
)

from .client_types import OpenBenchCounters


def compute_totals(db: DatabaseServicePort, *, tested_engine: str, base_engine: str) -> OpenBenchCounters:
    games = db.get_games_with_players(game_type="arena")
    relevant: list[GameRecordPlayers] = []
    totals = OpenBenchCounters()
    for game in games:
        black = coerce_str(game.get("black_player")) or ""
        white = coerce_str(game.get("white_player")) or ""
        if {black, white} != {tested_engine, base_engine}:
            continue
        result_raw = game["result"]
        relevant.append(game)

        # Diagnostic counters are independent of W/D/L scoring.
        if result_raw in {GameResult.BLACK_WIN_BY_TIMEOUT, GameResult.WHITE_WIN_BY_TIMEOUT}:
            totals.timelosses += 1
        if result_raw in {GameResult.BLACK_WIN_BY_ILLEGAL_MOVE, GameResult.WHITE_WIN_BY_ILLEGAL_MOVE}:
            totals.illegals += 1
        if result_raw in {GameResult.ERROR, GameResult.INVALID}:
            totals.crashes += 1

        # Crash-only: non-decided games (ERROR/INVALID -> crashes above, PAUSED -> nothing) are
        # excluded from W/D/L and from the pentanomial pairing, never folded into a draw.
        if not is_decisive_result(result_raw):
            continue
        is_tested_black = black == tested_engine
        score = tested_score(result_raw, is_tested_black=is_tested_black)
        if score >= 1.0:
            totals.wins += 1
        elif score <= 0.0:
            totals.losses += 1
        else:
            totals.draws += 1

    # Pentanomial: deterministic round-token pairing, two rounds per opening pair (tested view).
    totals.ll, totals.ld, totals.dd, totals.dw, totals.ww = compute_pentanomial_bins(
        relevant, tested_engine=tested_engine, base_engine=base_engine
    )
    return totals


__all__ = ["compute_totals"]
