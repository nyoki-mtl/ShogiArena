"""Shared base for pairwise runner dashboard APIs (match / SPRT)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
    DashboardRuntimeSupportPort,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int


class PairwiseRunnerAPI(ABC):
    """Common behavior for pairwise summary/timeline endpoints."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        summary_supplier: Callable[[], Mapping[str, JsonValue]] | None,
        mode: str,
        logger: logging.Logger,
        game_query: DashboardGameQueryPort | None = None,
        runtime_support: DashboardRuntimeSupportPort | None = None,
    ) -> None:
        interface_deps = load_dashboard_interface_dependencies()
        self._db_path = db_path
        self._run_dir = run_dir
        self._summary_supplier = summary_supplier
        self._mode = mode
        self._logger = logger
        self._game_query = game_query or interface_deps.game_query
        self._runtime_support = runtime_support or interface_deps.runtime_support

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=UTC).isoformat()

    def _load_run_state(self) -> JsonObject:
        return self._runtime_support.load_run_state(self._run_dir, logger=self._logger)

    def _get_summary_snapshot(self) -> Mapping[str, JsonValue]:
        if self._summary_supplier is None:
            return {}
        return self._summary_supplier() or {}

    def _resolve_engine_order(self) -> list[str]:
        run_state = self._load_run_state()
        config = run_state.get("config")
        if isinstance(config, Mapping):
            config_map = {str(key): value for key, value in config.items()}
            engines = config_map.get("engines")
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

        games = self._game_query.load_games(self._db_path)
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
        games = summary.get("games")
        if is_str_object_mapping(games):
            games_map = to_json_object(games)
            total_games_int = coerce_int(games_map.get("total"))
            if total_games_int is not None and total_games_int > 0:
                return total_games_int
        if completed_games > 0:
            return completed_games
        return None

    @staticmethod
    def _coerce_result(raw: GameResult | JsonValue | None) -> GameResult:
        return coerce_game_result(raw, is_strict=True)

    @abstractmethod
    def _build_payload(self) -> JsonObject:
        """Build mode-specific summary payload."""

    async def get_summary(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_payload()
        except Exception as exc:
            self._logger.exception("Failed to build %s summary", self._mode)
            return json_error_response(str(exc), status=500)
        return web.json_response(payload)

    async def get_timeline(self, _request: web.Request) -> web.Response:
        try:
            payload = self._build_payload()
        except Exception as exc:
            self._logger.exception("Failed to build %s timeline", self._mode)
            return json_error_response(str(exc), status=500)
        return web.json_response(
            {
                "mode": self._mode,
                "tested": payload.get("tested"),
                "baseline": payload.get("baseline"),
                "timeline": payload.get("timeline", []),
                "timestamp": payload.get("timestamp"),
            }
        )


__all__ = ["PairwiseRunnerAPI"]
