"""SPRT-specific API handlers for the arena dashboard."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import build_live_view_snapshot
from shogiarena._core.contexts.game_session.application.sprt_service import (
    PENTANOMIAL_MIN_PAIRS_FOR_LLR,
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    SPRT_MODEL_GSPRT_TRINOMIAL,
    Sprt,
    SprtDecision,
    SprtResult,
)
from shogiarena._core.interfaces.dashboard.game.pairwise_api import PairwiseConfigError, PairwiseRunnerAPI
from shogiarena._core.interfaces.dashboard.sprt.payloads import SprtTimelineEntry
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    round_index_from_game_name,
    tested_score,
)

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

    @staticmethod
    def _displayed_decision(status: SprtResult, min_games: int) -> str:
        # Mirror the runner, which only acts on a decision once games_played >= min_games. Below
        # that the LLR is shown raw (it may already cross a bound) but the decision stays CONTINUE.
        if status.games_played < min_games:
            return SprtDecision.CONTINUE.value
        return status.decision.value

    @staticmethod
    def _timeline_entry(index: int, status: SprtResult, min_games: int) -> SprtTimelineEntry:
        return {
            "game_index": index,
            "llr": status.llr,
            "lower": status.lower_bound,
            "upper": status.upper_bound,
            "decision": SprtAPI._displayed_decision(status, min_games),
            "wins": status.wins,
            "draws": status.draws,
            "losses": status.losses,
            "games": status.games_played,
            "win_rate": status.win_rate,
            "elo_estimate": status.elo_estimate,
            "pending_pairs": status.pending_pairs,
            "pending_games": status.pending_games,
        }

    def _build_payload(self) -> JsonObject:
        games = self._game_query.load_games(self._db_path)
        engines = self._resolve_engine_order()
        tested = engines[0] if len(engines) >= 1 else ""
        baseline = engines[1] if len(engines) >= 2 else ""

        config = self._resolve_sprt_config()
        # Use explicit None checks so that legitimate falsy values (e.g. elo1=0.0)
        # are preserved instead of being replaced by defaults.
        elo0_value = coerce_float(config.get("elo0"))
        elo0 = 0.0 if elo0_value is None else elo0_value
        elo1_value = coerce_float(config.get("elo1"))
        elo1 = 5.0 if elo1_value is None else elo1_value
        alpha_value = coerce_float(config.get("alpha"))
        alpha = 0.05 if alpha_value is None else alpha_value
        beta_value = coerce_float(config.get("beta"))
        beta = 0.05 if beta_value is None else beta_value
        min_games = coerce_int(config.get("min_games"))
        if min_games is None:
            min_games = coerce_int(config.get("min_games"))
        if min_games is None:
            min_games = 0
        max_games = coerce_int(config.get("max_games"))
        if max_games is None:
            max_games = coerce_int(config.get("max_games"))

        model = coerce_str(config.get("model")) or SPRT_MODEL_GSPRT_TRINOMIAL
        # Mirror the runner's pentanomial decision floor so the displayed LLR/decision matches the
        # stopping logic (ignored by the trinomial model).
        min_pairs = max(PENTANOMIAL_MIN_PAIRS_FOR_LLR, math.ceil(min_games / 2))
        try:
            sprt = Sprt(elo0=elo0, elo1=elo1, alpha=alpha, beta=beta, model=model, min_pairs=min_pairs)
        except ValueError as exc:
            raise PairwiseConfigError(str(exc), code="invalid_sprt_config") from exc
        is_pentanomial = model == SPRT_MODEL_GSPRT_PENTANOMIAL

        timeline: list[SprtTimelineEntry] = []
        skipped_non_decisive = 0
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

            # Only decisive results and genuine draws are valid SPRT observations. This is a
            # read-only replay, so non-game outcomes (ERROR/INVALID/PAUSED) are skipped and counted
            # rather than folded into draws (which would distort the LLR) or raised (which would
            # 500 the whole summary).
            if not (result.is_draw() or result.is_black_win() or result.is_white_win()):
                skipped_non_decisive += 1
                continue

            tested_is_black = black_engine == tested

            if is_pentanomial:
                game_name = game.get("game_name")
                round_idx = round_index_from_game_name(game_name) if isinstance(game_name, str) else None
                if round_idx is None:
                    continue  # cannot pair a game without a round token; skip it from the replay
                sfen = game.get("initial_sfen")
                status = sprt.add_game_observation(
                    sfen=str(sfen) if isinstance(sfen, str) else "startpos",
                    pair_slot=round_idx // 2,
                    is_tested_black=tested_is_black,
                    tested_score=tested_score(result, is_tested_black=tested_is_black),
                )
            else:
                tested_is_white = white_engine == tested
                if result.is_draw():
                    outcome = GameResult.DRAW_BY_REPETITION
                elif result.is_black_win():
                    outcome = GameResult.WHITE_WIN if tested_is_black else GameResult.BLACK_WIN
                else:  # is_white_win (non-decisive results were skipped above)
                    outcome = GameResult.WHITE_WIN if tested_is_white else GameResult.BLACK_WIN
                status = sprt.add_game_result(outcome)

            timeline.append(self._timeline_entry(index, status, min_games))

        status = sprt.get_status()
        completed_games = status.games_played
        total_games = max_games if max_games else self._resolve_total_games(completed_games)

        payload = {
            "mode": "sprt",
            "summary_source": "sprt",
            "tested": tested,
            "baseline": baseline,
            "config": {
                "model": model,
                "elo0": elo0,
                "elo1": elo1,
                "alpha": alpha,
                "beta": beta,
                "min_games": min_games,
                "max_games": max_games,
            },
            "status": {
                "llr": status.llr,
                "lower": status.lower_bound,
                "upper": status.upper_bound,
                "decision": self._displayed_decision(status, min_games),
                "wins": status.wins,
                "draws": status.draws,
                "losses": status.losses,
                "games": status.games_played,
                "win_rate": status.win_rate,
                "elo_estimate": status.elo_estimate,
                "pending_pairs": status.pending_pairs,
                "pending_games": status.pending_games,
            },
            "games": {
                "completed": completed_games,
                "total": total_games,
                "skipped_non_decisive": skipped_non_decisive,
            },
            "timeline": timeline,
            "timestamp": self._now_iso(),
        }
        payload_obj = to_json_object(payload)
        payload_obj["live_view"] = json_serialize(build_live_view_snapshot(payload_obj))
        return payload_obj
