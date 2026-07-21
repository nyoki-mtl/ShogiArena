"""Reporting/log output helpers for tournament summary finalization."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimate, BTDEstimator
from shogiarena._core.shared.kernel.statistics.confidence_intervals import normal_margin

logger = logging.getLogger(__name__)


class TournamentSummaryReportingService:
    """Handle final tournament logging and BTD estimation concerns."""

    @staticmethod
    def estimate_btd(
        *,
        games: Sequence[GameRecordPlayers],
        anchor_name: str | None,
        engine_names: Sequence[str],
    ) -> BTDEstimate:
        return BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)

    @staticmethod
    def log_tournament_results(results: TournamentResults) -> None:
        leaderboard = results.get_leaderboard()
        logger.debug("TOURNAMENT RESULTS")
        logger.debug("%-6s %-20s %-10s %-15s %-10s", "Rank", "Engine", "Points", "W/D/L", "Win %")
        for entry in leaderboard:
            wdl = f"{entry['wins']}/{entry['draws']}/{entry['losses']}"
            raw_win_rate = entry["win_rate"]
            win_rate = raw_win_rate if isinstance(raw_win_rate, int | float) else 0.0
            win_pct = f"{win_rate * 100:.1f}%"
            logger.debug(
                "%-6s %-20s %-10.1f %-15s %-10s",
                entry["rank"],
                entry["engine"],
                entry["points"],
                wdl,
                win_pct,
            )

    @staticmethod
    def log_btd_estimates(
        *,
        results: TournamentResults,
        btd: BTDEstimate,
        engine_names: Sequence[str],
    ) -> None:
        logger.debug("BTD RATING ESTIMATES (order-independent, with draws + color)")
        logger.debug("%-6s %-20s %-18s %-8s %-8s %-15s", "Rank", "Engine", "R±95%CI", "Games", "Points", "W/D/L")

        stats = results.engine_stats
        ratings_items = list(btd.ratings.items())
        if not ratings_items:
            ratings_items = [(name, 0.0) for name in engine_names]
        order = sorted(ratings_items, key=lambda x: (-float(x[1]), x[0]))
        for i, (name, rating) in enumerate(order, 1):
            se = btd.rating_se.get(name) or 0.0
            ci = normal_margin(se) or 0.0
            stat = stats.get(name, {"wins": 0, "draws": 0, "losses": 0})
            games_played = int(stat.get("wins", 0)) + int(stat.get("draws", 0)) + int(stat.get("losses", 0))
            points = float(stat.get("wins", 0)) + 0.5 * float(stat.get("draws", 0))
            wdl = f"{stat.get('wins', 0)}/{stat.get('draws', 0)}/{stat.get('losses', 0)}"
            logger.debug("%-6s %-20s %6.1f±%6.1f %-8d %-8.1f %-15s", i, name, rating, ci, games_played, points, wdl)

        gamma_ci = normal_margin(btd.gamma_elo_se) or 0.0
        draw_ci = normal_margin(btd.draw_eq_se) or 0.0
        logger.debug(
            "\nBlack advantage: %.1f±%.1f Elo | Draw(eq): %.1f%%±%.1f%%",
            btd.gamma_elo,
            gamma_ci,
            btd.draw_eq * 100,
            draw_ci * 100,
        )

        pairs: list[tuple[tuple[str, str], int]] = []
        for (engine_a, engine_b), pair_result in results.pair_results.items():
            games_ab = (
                int(pair_result.get(f"{engine_a}_wins", 0))
                + int(pair_result.get(f"{engine_b}_wins", 0))
                + int(pair_result.get("draws", 0))
            )
            pairs.append(((engine_a, engine_b), games_ab))
        pairs.sort(key=lambda x: -x[1])

        logger.debug("Head-to-Head (ΔR, 95% CI, LOS, W/D/L)")
        for (engine_a, engine_b), _n in pairs[:5]:
            pair_delta = btd.pair_delta(engine_a, engine_b, cov=btd.rating_cov)
            se = pair_delta.standard_error or 0.0
            ci = normal_margin(se) or 0.0
            los = pair_delta.likelihood_of_superiority
            pair_result = results.pair_results[(engine_a, engine_b)]
            wdl = (
                f"{pair_result.get(f'{engine_a}_wins', 0)}/"
                f"{pair_result.get('draws', 0)}/"
                f"{pair_result.get(f'{engine_b}_wins', 0)}"
            )
            header = f"{engine_a} vs {engine_b}: ΔR={pair_delta.delta_elo:+.1f}±{ci:.1f} Elo, "
            los_str = ("" if los is None else f"{los * 100:.1f}%").rjust(6)
            logger.debug(header + f"LOS={los_str}  W/D/L={wdl}")


__all__ = ["TournamentSummaryReportingService"]
