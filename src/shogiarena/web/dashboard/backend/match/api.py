"""Match-specific API handlers for the arena dashboard."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.utils.types.types import GameResult
from shogiarena.web.dashboard.backend.http_helpers import json_error_response
from shogiarena.web.dashboard.backend.live.schema import build_live_view_snapshot

logger = logging.getLogger(__name__)


class MatchAPI:
    """Expose match endpoints used by the dashboard."""

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
        app.router.add_get("/api/match/summary", self.get_summary)
        app.router.add_get("/api/match/timeline", self.get_timeline)

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
    def _elo_confidence_interval(win_rate: float | None, total: int) -> dict[str, float | None]:
        if win_rate is None or total <= 0:
            return {"lower": None, "upper": None}
        variance = win_rate * (1.0 - win_rate) / total
        std = math.sqrt(max(variance, 0.0))
        lower = max(0.0, win_rate - 1.96 * std)
        upper = min(1.0, win_rate + 1.96 * std)
        return {
            "lower": MatchAPI._win_rate_to_elo(lower),
            "upper": MatchAPI._win_rate_to_elo(upper),
        }

    @staticmethod
    def _win_rate_confidence_interval(win_rate: float | None, total: int) -> dict[str, float | None]:
        if win_rate is None or total <= 0:
            return {"lower": None, "upper": None}
        variance = win_rate * (1.0 - win_rate) / total
        std = math.sqrt(max(variance, 0.0))
        lower = max(0.0, win_rate - 1.96 * std)
        upper = min(1.0, win_rate + 1.96 * std)
        return {"lower": lower, "upper": upper}

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {"wins": 0, "losses": 0, "draws": 0, "games": 0}

    def _build_match_payload(self) -> dict[str, Any]:
        db_service = ArenaDBService(self._db_path)
        games = db_service.get_all_games()
        engines = self._resolve_engine_order()
        tested = engines[0] if len(engines) >= 1 else ""
        baseline = engines[1] if len(engines) >= 2 else ""

        summary_counts = self._empty_counts()
        black_counts = self._empty_counts()
        white_counts = self._empty_counts()

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
                    "gameIndex": index,
                    "wins": summary_counts["wins"],
                    "losses": summary_counts["losses"],
                    "draws": summary_counts["draws"],
                    "games": summary_counts["games"],
                    "winRate": total_rate,
                    "eloEstimate": self._win_rate_to_elo(total_rate),
                    "black": {
                        "wins": black_counts["wins"],
                        "losses": black_counts["losses"],
                        "draws": black_counts["draws"],
                        "games": black_counts["games"],
                        "winRate": black_rate,
                    },
                    "white": {
                        "wins": white_counts["wins"],
                        "losses": white_counts["losses"],
                        "draws": white_counts["draws"],
                        "games": white_counts["games"],
                        "winRate": white_rate,
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
            "summarySource": "match",
            "tested": tested,
            "baseline": baseline,
            "games": {
                "completed": summary_counts["games"],
                "total": total_games,
                "wins": summary_counts["wins"],
                "losses": summary_counts["losses"],
                "draws": summary_counts["draws"],
            },
            "winRate": win_rate,
            "winRateCi95": self._win_rate_confidence_interval(win_rate, summary_counts["games"]),
            "eloEstimate": self._win_rate_to_elo(win_rate),
            "eloCi95": self._elo_confidence_interval(win_rate, summary_counts["games"]),
            "colors": {
                "black": black_counts,
                "white": white_counts,
            },
            "timeline": timeline,
            "timestamp": self._now_iso(),
        }
        payload["liveView"] = build_live_view_snapshot(payload)
        return payload

    async def get_summary(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_match_payload()
        except Exception as exc:
            logger.exception("Failed to build match summary")
            return json_error_response(str(exc), status=500)
        return web.json_response(payload)

    async def get_timeline(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_match_payload()
        except Exception as exc:
            logger.exception("Failed to build match timeline")
            return json_error_response(str(exc), status=500)
        return web.json_response(
            {
                "mode": "match",
                "tested": payload.get("tested"),
                "baseline": payload.get("baseline"),
                "timeline": payload.get("timeline", []),
                "timestamp": payload.get("timestamp"),
            }
        )
