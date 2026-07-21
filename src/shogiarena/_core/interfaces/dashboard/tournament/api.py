"""Tournament-related API handlers for the arena dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shogiarena._core.contexts.dashboard.application.game.detail_builder import build_game_detail_payload
from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import attach_live_view_payload
from shogiarena._core.contexts.dashboard.application.tournament.payloads import (
    HeadToHeadPayload,
    PairStatsPayload,
    ProgressPayload,
    StandingsPayload,
)
from shogiarena._core.contexts.dashboard.application.tournament.stats_builders import (
    build_head_to_head_payload,
    build_pair_stats_payload,
)
from shogiarena._core.contexts.dashboard.application.tournament.summary_builders import (
    build_progress_payload,
    build_standings_payload,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.interfaces.boundaries.parsers.tournament import parse_tournament_payload
from shogiarena._core.interfaces.dashboard.api_query_models import (
    PaginatedSearchQuery,
    SummaryStreamQuery,
)
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse import extract_last_event_id, inject_resume_from, serialize_sse_event
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineOptionsSnapshots

logger = logging.getLogger(__name__)


class _MatchHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TournamentAPI:
    """Expose tournament endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        engine_options_supplier: Callable[[], EngineOptionsSnapshots] | None = None,
        engine_info_supplier: Callable[[], dict[str, dict[str, str]]] | None = None,
        game_query: DashboardGameQueryPort | None = None,
    ) -> None:
        interface_dependencies = load_dashboard_interface_dependencies()
        self._db_path = db_path
        self._run_dir = run_dir
        self._engine_options_supplier = engine_options_supplier
        self._engine_info_supplier = engine_info_supplier
        self._tournament_sse_seq = 0
        self._game_query = game_query or interface_dependencies.game_query

    def _next_tournament_seq(self) -> int:
        self._tournament_sse_seq += 1
        return self._tournament_sse_seq

    def _standings_payload(self) -> StandingsPayload:
        return build_standings_payload(
            db_path=self._db_path,
            run_dir=self._run_dir,
            game_query=self._game_query,
        )

    def _progress_payload(self) -> ProgressPayload:
        return build_progress_payload(
            db_path=self._db_path,
            run_dir=self._run_dir,
            logger=logger,
            game_query=self._game_query,
        )

    def _head_to_head_payload(self) -> HeadToHeadPayload:
        return build_head_to_head_payload(self._game_query.load_games(self._db_path))

    def _pair_stats_payload(self) -> PairStatsPayload:
        return build_pair_stats_payload(self._game_query.load_games(self._db_path))

    def register_routes(self, app: web.Application) -> None:
        """Register tournament endpoints on the aiohttp app."""
        app.router.add_get("/api/games", self.get_games)
        app.router.add_get("/api/game/{game_id}", self.get_game)
        app.router.add_get("/api/standings", self.get_standings)
        app.router.add_get("/api/head_to_head", self.get_head_to_head)
        app.router.add_get("/api/progress", self.get_progress)
        app.router.add_get("/api/tournament/summary/stream", self.sse_tournament_summary)
        app.router.add_get("/api/engine_options/{engine_name}", self.get_engine_options)
        app.router.add_get("/api/tournament/match-history", self.get_match_history)
        app.router.add_get("/api/tournament/pair-stats", self.get_pair_stats)

    async def get_games(self, request: web.Request) -> web.Response:
        try:
            parsed_query = PaginatedSearchQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        offset = parsed_query.offset
        limit = parsed_query.limit
        search_query = parsed_query.q

        raw_payload = self._game_query.build_games_raw_payload(
            self._db_path,
            limit=limit,
            offset=offset,
            search_query=search_query,
        )
        try:
            payload = parse_tournament_payload(
                "games_list",
                raw_payload,
                path="api.games",
            )
        except ContractParseError as exc:
            logger.exception("Invalid tournament games payload: %s", exc)
            return json_error_response(
                "Tournament games payload validation failed", status=500, code="internal_contract_error"
            )
        return web.json_response(payload)

    async def get_match_history(self, request: web.Request) -> web.Response:
        try:
            parsed_query = _MatchHistoryQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        limit = parsed_query.limit
        offset = parsed_query.offset

        raw_payload = self._game_query.build_match_history_raw_payload(self._db_path, limit=limit, offset=offset)
        try:
            payload = parse_tournament_payload(
                "match_history",
                raw_payload,
                path="api.match_history",
            )
        except ContractParseError as exc:
            logger.exception("Invalid tournament match history payload: %s", exc)
            return json_error_response(
                "Tournament match_history payload validation failed",
                status=500,
                code="internal_contract_error",
            )
        return web.json_response(payload)

    async def get_game(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]

        record = self._game_query.load_game_record(self._db_path, game_name=game_id)
        if record is not None:
            raw_payload = build_game_detail_payload(record=record, game_id=game_id, logger=logger)
            try:
                payload = parse_tournament_payload(
                    "game",
                    raw_payload,
                    path="api.game",
                )
            except ContractParseError as exc:
                logger.exception("Invalid tournament game payload: %s", exc)
                return json_error_response(
                    "Tournament game payload validation failed", status=500, code="internal_contract_error"
                )
            return web.json_response(payload)

        return json_error_response("Game not found", status=404, code="game_not_found")

    async def get_standings(self, _request: web.Request) -> web.Response:
        try:
            payload = parse_tournament_payload(
                "standings",
                self._standings_payload(),
                path="api.standings",
            )
        except ContractParseError as exc:
            logger.exception("Invalid tournament standings payload: %s", exc)
            return json_error_response(
                "Tournament standings payload validation failed", status=500, code="internal_contract_error"
            )
        return web.json_response(payload)

    async def get_head_to_head(self, _request: web.Request) -> web.Response:
        try:
            payload = parse_tournament_payload(
                "head_to_head",
                self._head_to_head_payload(),
                path="api.head_to_head",
            )
        except ContractParseError as exc:
            logger.exception("Invalid head-to-head payload: %s", exc)
            return json_error_response(
                "Tournament head_to_head payload validation failed",
                status=500,
                code="internal_contract_error",
            )
        return web.json_response(payload)

    async def get_pair_stats(self, _request: web.Request) -> web.Response:
        try:
            payload = parse_tournament_payload(
                "pair_stats",
                self._pair_stats_payload(),
                path="api.pair_stats",
            )
        except ContractParseError as exc:
            logger.exception("Invalid pair stats payload: %s", exc)
            return json_error_response(
                "Tournament pair_stats payload validation failed",
                status=500,
                code="internal_contract_error",
            )
        return web.json_response(payload)

    async def get_progress(self, _request: web.Request) -> web.Response:
        try:
            payload = parse_tournament_payload("progress", self._progress_payload(), path="api.progress")
        except ContractParseError as exc:
            logger.exception("Invalid tournament progress payload: %s", exc)
            return json_error_response(
                "Tournament progress payload validation failed", status=500, code="internal_contract_error"
            )
        return web.json_response(payload)

    async def sse_tournament_summary(self, request: web.Request) -> web.StreamResponse:
        try:
            parsed_query = SummaryStreamQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        last_event_id = extract_last_event_id(request)
        poll_interval = parsed_query.poll_interval
        poll_interval = max(1.0, min(poll_interval, 30.0))
        should_send_initial = parsed_query.should_send_initial
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: JsonObject) -> None:
            payload = inject_resume_from(payload, last_event_id)
            seq = self._next_tournament_seq()
            envelope: dict[str, object] = dict(payload)
            envelope.setdefault("stream", "tournament_summary")
            envelope["seq"] = seq
            parsed = parse_tournament_payload(
                "stream",
                envelope,
                path="api.tournament_summary",
            )
            chunk = serialize_sse_event("tournament_summary", parsed, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                standings_payload = parse_tournament_payload(
                    "standings",
                    self._standings_payload(),
                    path="api.tournament_summary.standings",
                )
                progress_payload = parse_tournament_payload(
                    "progress",
                    self._progress_payload(),
                    path="api.tournament_summary.progress",
                )
                summary_payload = attach_live_view_payload(
                    {
                        **standings_payload,
                        **progress_payload,
                    }
                )
                signature = json.dumps(summary_payload, sort_keys=True, ensure_ascii=False)
                if should_send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "summary_update",
                            "data": summary_payload,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    should_send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except ContractParseError as exc:
            logger.exception("Tournament summary stream contract validation failed: %s", exc)
            raise web.HTTPInternalServerError(reason="Tournament summary stream contract validation failed") from exc
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("Tournament summary SSE client disconnected")

        return response

    async def get_engine_options(self, request: web.Request) -> web.Response:
        engine_name = request.match_info["engine_name"]
        supplier = self._engine_options_supplier
        options_map = supplier() if callable(supplier) else {}
        options = options_map.get(engine_name)
        if options is None:
            decoded = unquote(engine_name)
            if decoded != engine_name:
                engine_name = decoded
                options = options_map.get(engine_name)
        if options is None:
            lowered = engine_name.lower()
            for key, value in options_map.items():
                if key.lower() == lowered:
                    engine_name = key
                    options = value
                    break
        if options is None:
            return json_error_response(
                f"Engine '{engine_name}' options not found",
                status=404,
                code="engine_not_found",
                engine=engine_name,
                options={},
            )

        info_supplier = self._engine_info_supplier
        info_map = info_supplier() if callable(info_supplier) else {}
        info = info_map.get(engine_name, {})
        raw_payload: dict[str, object] = {
            "engine": engine_name,
            "options": options,
            "info": info,
            "updated_at": datetime.now().isoformat(),
        }
        try:
            payload = parse_tournament_payload(
                "engine_options",
                raw_payload,
                path="api.engine_options",
            )
        except ContractParseError as exc:
            logger.exception("Invalid tournament engine options payload: %s", exc)
            return json_error_response(
                "Tournament engine_options payload validation failed", status=500, code="internal_contract_error"
            )
        return web.json_response(payload)
