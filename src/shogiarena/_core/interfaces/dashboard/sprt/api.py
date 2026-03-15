"""SPRT-specific API handlers for the arena dashboard."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import build_live_view_snapshot
from shogiarena._core.contexts.game_session.application.sprt_service import Sprt
from shogiarena._core.interfaces.dashboard.game.pairwise_api import PairwiseRunnerAPI
from shogiarena._core.interfaces.dashboard.sprt.payloads import SprtTimelineEntry
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)


class SprtAPI(PairwiseRunnerAPI):
    """Expose SPRT endpoints used by the dashboard."""

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
            mode="sprt",
            logger=logger,
        )

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/sprt/summary", self.get_summary)
        app.router.add_get("/api/sprt/timeline", self.get_timeline)

    def _resolve_sprt_config(self) -> JsonObject:
        run_state = self._load_run_state()
        config = run_state.get("config")
        if is_str_object_mapping(config):
            config_map = {str(key): value for key, value in config.items()}
            sprt_conf = config_map.get("sprt")
            if is_str_object_mapping(sprt_conf):
                return to_json_object(sprt_conf)

        summary = self._get_summary_snapshot()
        sprt_summary = summary.get("sprt") if isinstance(summary, Mapping) else None
        if is_str_object_mapping(sprt_summary):
            sprt_map = to_json_object(sprt_summary)
            if any(key in sprt_map for key in ("elo0", "elo1", "alpha", "beta")):
                return sprt_map

        return {}

    def _build_payload(self) -> JsonObject:
        games = self._game_query.load_games(self._db_path)
        engines = self._resolve_engine_order()
        tested = engines[0] if len(engines) >= 1 else ""
        baseline = engines[1] if len(engines) >= 2 else ""

        config = self._resolve_sprt_config()
        elo0 = coerce_float(config.get("elo0")) or 0.0
        elo1 = coerce_float(config.get("elo1")) or 5.0
        alpha = coerce_float(config.get("alpha")) or 0.05
        beta = coerce_float(config.get("beta")) or 0.05
        min_games = coerce_int(config.get("min_games"))
        if min_games is None:
            min_games = coerce_int(config.get("minGames"))
        if min_games is None:
            min_games = 0
        max_games = coerce_int(config.get("max_games"))
        if max_games is None:
            max_games = coerce_int(config.get("maxGames"))

        sprt = Sprt(elo0=elo0, elo1=elo1, alpha=alpha, beta=beta)

        timeline: list[SprtTimelineEntry] = []
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

            tested_is_black = black_engine == tested
            tested_is_white = white_engine == tested

            if result.is_draw():
                outcome = GameResult.DRAW_BY_REPETITION
            elif result.is_black_win():
                outcome = GameResult.WHITE_WIN if tested_is_black else GameResult.BLACK_WIN
            elif result.is_white_win():
                outcome = GameResult.WHITE_WIN if tested_is_white else GameResult.BLACK_WIN
            else:
                outcome = GameResult.DRAW_BY_REPETITION

            status = sprt.add_game_result(outcome)
            timeline.append(
                {
                    "gameIndex": index,
                    "llr": status.llr,
                    "lower": status.lower_bound,
                    "upper": status.upper_bound,
                    "decision": status.decision.value,
                    "wins": status.wins,
                    "draws": status.draws,
                    "losses": status.losses,
                    "games": status.games_played,
                    "winRate": status.win_rate,
                    "eloEstimate": status.elo_estimate,
                }
            )

        status = sprt.get_status()
        completed_games = status.games_played
        total_games = max_games if max_games else self._resolve_total_games(completed_games)

        payload = {
            "mode": "sprt",
            "summarySource": "sprt",
            "tested": tested,
            "baseline": baseline,
            "config": {
                "elo0": elo0,
                "elo1": elo1,
                "alpha": alpha,
                "beta": beta,
                "minGames": min_games,
                "maxGames": max_games,
            },
            "status": {
                "llr": status.llr,
                "lower": status.lower_bound,
                "upper": status.upper_bound,
                "decision": status.decision.value,
                "wins": status.wins,
                "draws": status.draws,
                "losses": status.losses,
                "games": status.games_played,
                "winRate": status.win_rate,
                "eloEstimate": status.elo_estimate,
            },
            "games": {
                "completed": completed_games,
                "total": total_games,
            },
            "timeline": timeline,
            "timestamp": self._now_iso(),
        }
        payload_obj = to_json_object(payload)
        payload_obj["liveView"] = json_serialize(build_live_view_snapshot(payload_obj))
        return payload_obj
