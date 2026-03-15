"""Domain models for runtime summary/result aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.wdl_counts import WdlCounts


class EngineWdlCounts(WdlCounts, total=False):
    games: int


@dataclass
class TournamentResults:
    engine_stats: dict[str, EngineWdlCounts]
    pair_results: dict[tuple[str, str], dict[str, int]]
    completed_games: list[str]
    total_games: int
    completed_games_count: int
    cancelled_games_count: int = 0

    def get_leaderboard(self) -> list[JsonObject]:
        leaderboard: list[JsonObject] = []
        for engine, stats in self.engine_stats.items():
            points = stats["wins"] + 0.5 * stats["draws"]
            games = stats.get("games")
            if games is None:
                games = stats["wins"] + stats["losses"] + stats["draws"]
            win_rate = stats["wins"] / games if games > 0 else 0
            leaderboard.append(
                {
                    "rank": 0,
                    "engine": engine,
                    "points": points,
                    "games": games,
                    "wins": stats["wins"],
                    "draws": stats["draws"],
                    "losses": stats["losses"],
                    "win_rate": win_rate,
                }
            )

        def _sort_key(x: JsonObject) -> tuple[float, float]:
            points_raw = x.get("points")
            win_rate_raw = x.get("win_rate")
            points = (
                float(points_raw) if isinstance(points_raw, int | float) and not isinstance(points_raw, bool) else 0.0
            )
            win_rate = (
                float(win_rate_raw)
                if isinstance(win_rate_raw, int | float) and not isinstance(win_rate_raw, bool)
                else 0.0
            )
            return (-points, -win_rate)

        leaderboard.sort(key=_sort_key)
        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i
        return leaderboard


__all__ = ["EngineWdlCounts", "TournamentResults", "WdlCounts"]
