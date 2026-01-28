"""SPRT-specific API handlers for the arena dashboard."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.statistics.sprt import Sprt
from shogiarena.utils.types.types import GameResult
from shogiarena.web.dashboard.backend.http_helpers import json_error_response
from shogiarena.web.dashboard.backend.live.schema import build_live_view_snapshot

logger = logging.getLogger(__name__)


class SprtAPI:
    """Expose SPRT endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        summary_supplier: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        self._summary_supplier = summary_supplier

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/sprt/summary", self.get_summary)
        app.router.add_get("/api/sprt/timeline", self.get_timeline)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def _load_run_state(self) -> dict[str, Any]:
        run_state_path = self._run_dir / "run_state.json"
        if not run_state_path.exists():
            return {}
        try:
            return json.loads(run_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed to read run_state.json: %s", exc, exc_info=True)
            return {}

    def _get_summary_snapshot(self) -> Mapping[str, Any]:
        if self._summary_supplier is None:
            return {}
        try:
            return self._summary_supplier() or {}
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to load summary snapshot: %s", exc, exc_info=True)
            return {}

    def _resolve_engine_order(self) -> list[str]:
        run_state = self._load_run_state()
        config = run_state.get("config")
        if isinstance(config, dict):
            engines = config.get("engines")
            if isinstance(engines, list):
                ordered = [str(name) for name in engines if str(name).strip()]
                if len(ordered) >= 2:
                    return ordered

        summary = self._get_summary_snapshot()
        engines = summary.get("engines")
        if isinstance(engines, list):
            ordered = [str(name) for name in engines if str(name).strip()]
            if len(ordered) >= 2:
                return ordered

        db_service = ArenaDBService(self._db_path)
        games = db_service.get_all_games()
        names: list[str] = []
        for game in games:
            for key in ("black_engine", "white_engine"):
                name = game.get(key)
                if isinstance(name, str) and name.strip() and name not in names:
                    names.append(name)
        return names

    def _resolve_total_games(self, completed_games: int) -> int | None:
        run_state = self._load_run_state()
        total = run_state.get("original_total_games") or run_state.get("total_games")
        if isinstance(total, int) and total > 0:
            return total

        summary = self._get_summary_snapshot()
        games = summary.get("games") if isinstance(summary, Mapping) else None
        if isinstance(games, Mapping):
            total_games = games.get("total")
            try:
                total_games_int = int(total_games) if total_games is not None else None
            except (TypeError, ValueError):
                total_games_int = None
            if total_games_int is not None and total_games_int > 0:
                return total_games_int
        if completed_games > 0:
            return completed_games
        return None

    @staticmethod
    def _coerce_result(raw: Any) -> GameResult:
        if isinstance(raw, GameResult):
            return raw
        if isinstance(raw, int):
            return GameResult(raw)
        if isinstance(raw, str) and raw.strip():
            return GameResult(int(raw))
        return GameResult.DRAW_BY_REPETITION

    def _resolve_sprt_config(self) -> dict[str, Any]:
        run_state = self._load_run_state()
        config = run_state.get("config")
        if isinstance(config, dict):
            sprt_conf = config.get("sprt")
            if isinstance(sprt_conf, dict):
                return sprt_conf

        summary = self._get_summary_snapshot()
        sprt_summary = summary.get("sprt") if isinstance(summary, Mapping) else None
        if isinstance(sprt_summary, dict):
            if any(key in sprt_summary for key in ("elo0", "elo1", "alpha", "beta")):
                return sprt_summary

        return {}

    def _build_sprt_payload(self) -> dict[str, Any]:
        db_service = ArenaDBService(self._db_path)
        games = db_service.get_all_games()
        engines = self._resolve_engine_order()
        tested = engines[0] if len(engines) >= 1 else ""
        baseline = engines[1] if len(engines) >= 2 else ""

        config = self._resolve_sprt_config()
        elo0 = float(config.get("elo0", 0.0) or 0.0)
        elo1 = float(config.get("elo1", 5.0) or 5.0)
        alpha = float(config.get("alpha", 0.05) or 0.05)
        beta = float(config.get("beta", 0.05) or 0.05)
        min_games = int(config.get("min_games", config.get("minGames", 0)) or 0)
        max_games = config.get("max_games", config.get("maxGames"))
        if max_games is not None:
            try:
                max_games = int(max_games)
            except (TypeError, ValueError):
                max_games = None

        sprt = Sprt(elo0=elo0, elo1=elo1, alpha=alpha, beta=beta)

        timeline: list[dict[str, Any]] = []
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
        payload["liveView"] = build_live_view_snapshot(payload)
        return payload

    async def get_summary(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_sprt_payload()
        except Exception as exc:
            logger.exception("Failed to build SPRT summary")
            return json_error_response(str(exc), status=500)
        return web.json_response(payload)

    async def get_timeline(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_sprt_payload()
        except Exception as exc:
            logger.exception("Failed to build SPRT timeline")
            return json_error_response(str(exc), status=500)
        return web.json_response(
            {
                "mode": "sprt",
                "tested": payload.get("tested"),
                "baseline": payload.get("baseline"),
                "timeline": payload.get("timeline", []),
                "timestamp": payload.get("timestamp"),
            }
        )
