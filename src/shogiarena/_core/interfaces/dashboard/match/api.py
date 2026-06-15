"""Match-specific API handlers for the arena dashboard."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import build_live_view_snapshot
from shogiarena._core.interfaces.dashboard.game.pairwise_api import PairwiseRunnerAPI
from shogiarena._core.interfaces.dashboard.match.payloads import (
    ConfidenceInterval,
    MatchTimelineEntry,
    WdlGamesCount,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)


class MatchAPI(PairwiseRunnerAPI):
    """Expose match endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        summary_supplier: Callable[[], Mapping[str, JsonValue]] | None = None,
    ) -> None:
        super().__init__(
            db_path=db_path,
            run_dir=run_dir,
            summary_supplier=summary_supplier,
            mode="match",
            logger=logger,
        )

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/match/summary", self.get_summary)
        app.router.add_get("/api/match/timeline", self.get_timeline)

    @staticmethod
    def _safe_win_rate(wins: int, draws: int, total: int) -> float | None:
        if total <= 0:
            return None
        return (wins + 0.5 * draws) / total

    @staticmethod
    def _win_rate_to_elo(win_rate: float | None) -> float | None:
        if win_rate is None:
            return None
        bounded = max(1e-4, min(1 - 1e-4, win_rate))
        return 400.0 * math.log10(bounded / (1.0 - bounded))

    @staticmethod
    def _score_std_error(counts: WdlGamesCount) -> float | None:
        """Standard error of the mean game score using the {1, 0.5, 0} sample variance.

        Using the draw-aware score distribution instead of a Bernoulli p*(1-p) variance avoids
        over-estimating the interval when draws are frequent.
        """
        total = counts["games"]
        if total <= 0:
            return None
        mean = (counts["wins"] + 0.5 * counts["draws"]) / total
        # Per-game score variance: E[s^2] - mean^2 for s in {0, 0.5, 1}.
        second_moment = (counts["wins"] + 0.25 * counts["draws"]) / total
        variance = max(second_moment - mean * mean, 0.0)
        return math.sqrt(variance / total)

    @staticmethod
    def _elo_confidence_interval(win_rate: float | None, counts: WdlGamesCount) -> ConfidenceInterval:
        std = MatchAPI._score_std_error(counts)
        if win_rate is None or std is None:
            return {"lower": None, "upper": None}
        lower = max(0.0, win_rate - 1.96 * std)
        upper = min(1.0, win_rate + 1.96 * std)
        return {
            "lower": MatchAPI._win_rate_to_elo(lower),
            "upper": MatchAPI._win_rate_to_elo(upper),
        }

    @staticmethod
    def _win_rate_confidence_interval(win_rate: float | None, counts: WdlGamesCount) -> ConfidenceInterval:
        std = MatchAPI._score_std_error(counts)
        if win_rate is None or std is None:
            return {"lower": None, "upper": None}
        lower = max(0.0, win_rate - 1.96 * std)
        upper = min(1.0, win_rate + 1.96 * std)
        return {"lower": lower, "upper": upper}

    @staticmethod
    def _empty_counts() -> WdlGamesCount:
        return {"wins": 0, "losses": 0, "draws": 0, "games": 0}

    def _build_payload(self) -> JsonObject:
        games = self._game_query.load_games(self._db_path)
        engines = self._resolve_engine_order()
        tested = engines[0] if len(engines) >= 1 else ""
        baseline = engines[1] if len(engines) >= 2 else ""

        summary_counts = self._empty_counts()
        black_counts = self._empty_counts()
        white_counts = self._empty_counts()

        timeline: list[MatchTimelineEntry] = []
        for index, game in enumerate(games, start=1):
            black_engine = game.get("black_engine")
            white_engine = game.get("white_engine")
            result = self._coerce_result(game.get("result"))
            if not isinstance(black_engine, str) or not isinstance(white_engine, str):
                continue
            if tested not in {black_engine, white_engine}:
                continue
            if baseline not in {black_engine, white_engine} and baseline:
                continue

            summary_counts["games"] += 1
            if result.is_draw():
                summary_counts["draws"] += 1
            elif result.is_black_win():
                if black_engine == tested:
                    summary_counts["wins"] += 1
                else:
                    summary_counts["losses"] += 1
            elif result.is_white_win():
                if white_engine == tested:
                    summary_counts["wins"] += 1
                else:
                    summary_counts["losses"] += 1
            else:
                summary_counts["draws"] += 1

            if black_engine == tested:
                black_counts["games"] += 1
                if result.is_draw():
                    black_counts["draws"] += 1
                elif result.is_black_win():
                    black_counts["wins"] += 1
                elif result.is_white_win():
                    black_counts["losses"] += 1
                else:
                    black_counts["draws"] += 1
            elif white_engine == tested:
                white_counts["games"] += 1
                if result.is_draw():
                    white_counts["draws"] += 1
                elif result.is_white_win():
                    white_counts["wins"] += 1
                elif result.is_black_win():
                    white_counts["losses"] += 1
                else:
                    white_counts["draws"] += 1

            total_rate = self._safe_win_rate(
                summary_counts["wins"],
                summary_counts["draws"],
                summary_counts["games"],
            )
            black_rate = self._safe_win_rate(
                black_counts["wins"],
                black_counts["draws"],
                black_counts["games"],
            )
            white_rate = self._safe_win_rate(
                white_counts["wins"],
                white_counts["draws"],
                white_counts["games"],
            )

            timeline.append(
                {
                    "game_index": index,
                    "wins": summary_counts["wins"],
                    "losses": summary_counts["losses"],
                    "draws": summary_counts["draws"],
                    "games": summary_counts["games"],
                    "win_rate": total_rate,
                    "elo_estimate": self._win_rate_to_elo(total_rate),
                    "black": {
                        "wins": black_counts["wins"],
                        "losses": black_counts["losses"],
                        "draws": black_counts["draws"],
                        "games": black_counts["games"],
                        "win_rate": black_rate,
                    },
                    "white": {
                        "wins": white_counts["wins"],
                        "losses": white_counts["losses"],
                        "draws": white_counts["draws"],
                        "games": white_counts["games"],
                        "win_rate": white_rate,
                    },
                }
            )

        total_games = self._resolve_total_games(summary_counts["games"])
        win_rate = self._safe_win_rate(
            summary_counts["wins"],
            summary_counts["draws"],
            summary_counts["games"],
        )
        payload = {
            "mode": "match",
            "summary_source": "match",
            "tested": tested,
            "baseline": baseline,
            "games": {
                "completed": summary_counts["games"],
                "total": total_games,
                "wins": summary_counts["wins"],
                "losses": summary_counts["losses"],
                "draws": summary_counts["draws"],
            },
            "win_rate": win_rate,
            "win_rate_ci95": self._win_rate_confidence_interval(win_rate, summary_counts),
            "elo_estimate": self._win_rate_to_elo(win_rate),
            "elo_ci95": self._elo_confidence_interval(win_rate, summary_counts),
            "colors": {
                "black": black_counts,
                "white": white_counts,
            },
            "timeline": timeline,
            "timestamp": self._now_iso(),
        }
        payload_obj = to_json_object(payload)
        payload_obj["live_view"] = json_serialize(build_live_view_snapshot(payload_obj))
        return payload_obj
