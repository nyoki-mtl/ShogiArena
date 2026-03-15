"""SPSA-related API handlers for the arena dashboard."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.application.game.detail_builder import build_game_detail_payload
from shogiarena._core.contexts.dashboard.application.live.snapshot_builder import build_live_view_snapshot
from shogiarena._core.contexts.dashboard.application.spsa.analysis_cache import AnalysisCacheService
from shogiarena._core.contexts.dashboard.application.spsa.detail_payload_filter import (
    apply_detail_view,
    parse_detail_view_config,
)
from shogiarena._core.contexts.dashboard.application.spsa.variant_resolution import (
    extract_variant_from_game_id,
    resolve_variant_id,
)
from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
    DashboardSpsaSupportPort,
)
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaAnalysisPort,
    DashboardSpsaGameListingPort,
    DashboardSpsaLtcServicePort,
    DashboardSpsaParamsServicePort,
    DashboardSpsaStorePort,
    DashboardSpsaSummaryServicePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.application.dashboard.snapshot_composition import SpsaSnapshotCompositionService
from shogiarena._core.contexts.spsa.ports.dashboard_factory import DashboardSpsaServicesFactory
from shogiarena._core.interfaces.dashboard.api_query_models import PaginatedSearchQuery
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text

from .api_query_models import (
    LtcLimitQuery,
    LtcResultsQuery,
    SpsaEventsQuery,
    SpsaParamsQuery,
    SpsaUpdatePath,
    SpsaUpdatesQuery,
)
from .service_resolution import resolve_dashboard_interface_dependencies, resolve_dashboard_spsa_services
from .stream.streams import SpsaStreams

logger = logging.getLogger(__name__)


class SpsaAPI:
    """Expose SPSA endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        dashboard_service_factory: DashboardSpsaServicesFactory | None = None,
        store: DashboardSpsaStorePort | None = None,
        summary_service: DashboardSpsaSummaryServicePort | None = None,
        update_query_service: DashboardSpsaUpdateQueryPort | None = None,
        game_listing_service: DashboardSpsaGameListingPort | None = None,
        analysis_service: DashboardSpsaAnalysisPort | None = None,
        spsa_support: DashboardSpsaSupportPort | None = None,
        game_query: DashboardGameQueryPort | None = None,
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        resolved_services = resolve_dashboard_spsa_services(
            db_path=db_path,
            run_dir=run_dir,
            dashboard_service_factory=dashboard_service_factory,
            store=store,
            summary_service=summary_service,
            update_query_service=update_query_service,
            game_listing_service=game_listing_service,
            analysis_service=analysis_service,
        )
        self._store = resolved_services.store
        self._summary_service = resolved_services.summary_service
        self._update_query_service = resolved_services.update_query_service
        self._game_listing_service = resolved_services.game_listing_service
        self._analysis_service = resolved_services.analysis_service
        interface_dependencies = resolve_dashboard_interface_dependencies(
            spsa_support=spsa_support,
            game_query=game_query,
        )
        self._game_query = interface_dependencies.game_query
        self._ltc_service: DashboardSpsaLtcServicePort = interface_dependencies.spsa_support.create_ltc_service(
            self._store
        )
        self._snapshot_service = SpsaSnapshotCompositionService(
            store=self._store,
            ltc_service=self._ltc_service,
            summary_service=self._summary_service,
            live_view_builder=build_live_view_snapshot,
        )
        self._analysis_cache = AnalysisCacheService(
            update_query_service=self._update_query_service,
            analysis_service=self._analysis_service,
        )
        self._streams = SpsaStreams(
            self._update_query_service,
            analysis_service=self._analysis_service,
            store=self._store,
            ltc_service=self._ltc_service,
            snapshot_service=self._snapshot_service,
            summary_supplier=self._build_summary_payload,
            analysis_cache=self._analysis_cache,
        )
        self._params_service: DashboardSpsaParamsServicePort = (
            interface_dependencies.spsa_support.create_params_service(
                store=self._store,
                run_dir=run_dir,
            )
        )

    # ------------------------------------------------------------------
    # Route registration helpers
    # ------------------------------------------------------------------
    def register_routes(self, app: web.Application) -> None:
        """Register SPSA endpoints on the given aiohttp application."""
        app.router.add_get("/api/spsa/events", self.get_events)
        app.router.add_get("/api/spsa/summary", self.get_summary)
        app.router.add_get("/api/spsa/params", self.get_params)
        app.router.add_get("/api/spsa/update/{idx}", self.get_update)
        app.router.add_get("/api/spsa/updates", self.get_updates)
        app.router.add_get("/api/spsa/updates/stream", self._streams.sse_updates)
        app.router.add_get("/api/spsa/update/detail/stream", self._streams.sse_update_detail)
        app.router.add_get("/api/spsa/variants", self.get_variants)
        app.router.add_get("/api/spsa/variant/{variant_id}", self.get_variant)
        app.router.add_get("/api/spsa/variant/games/stream", self._streams.sse_variant_games)
        app.router.add_get("/ws/spsa/updates", self._streams.websocket_updates)
        app.router.add_get("/api/spsa/analysis/correlation", self.get_correlation)
        app.router.add_get("/api/spsa/analysis/correlation/stream", self._streams.sse_correlation)
        app.router.add_get("/api/spsa/analysis/convergence", self.sse_convergence)
        app.router.add_get("/api/spsa/games", self.get_games)
        app.router.add_get("/api/spsa/game/{game_id}", self.get_game)
        app.router.add_get("/api/spsa/ltc/summary", self.get_ltc_summary)
        app.router.add_get("/api/spsa/ltc/results", self.get_ltc_results)
        app.router.add_get("/api/spsa/ltc/results/stream", self._streams.sse_ltc_results)
        app.router.add_get("/api/spsa/ltc/progress/stream", self._streams.sse_ltc_progress)
        app.router.add_get("/api/spsa/ltc/games/stream", self._streams.sse_ltc_games)
        app.router.add_get("/api/spsa/summary/stream", self._streams.sse_summary)

    # ------------------------------------------------------------------
    # GET /api/spsa/events
    # ------------------------------------------------------------------
    async def get_events(self, request: web.Request) -> web.Response:
        try:
            parsed_query = SpsaEventsQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_limit")
        limit = parsed_query.limit
        event_entries = self._store.load_event_entries()
        if not event_entries:
            return web.json_response({"events": []})

        events: list[JsonObject] = []
        for event_data in event_entries[-limit:]:
            event_base = {k: v for k, v in event_data.items() if k != "payload"}
            payload = dict(event_base)
            event_base["type"] = event_data.get("event", "event")
            event_base["timestamp"] = event_base.get("ts", coerce_int(time.time() * 1000.0) or 0)
            event_base["payload"] = payload
            events.append(event_base)

        return web.json_response({"events": events})

    # ------------------------------------------------------------------
    # GET /api/spsa/summary
    # ------------------------------------------------------------------
    async def get_summary(self, _request: web.Request) -> web.Response:
        summary_payload = self._build_summary_payload()
        return web.json_response(summary_payload)

    def _build_summary_payload(self) -> JsonObject:
        return self._snapshot_service.build_summary_payload()

    def _attach_ltc_summary_fields(
        self,
        snapshot: Mapping[str, JsonValue],
        *,
        ltc_summary: Mapping[str, JsonValue] | None = None,
    ) -> JsonObject:
        return self._snapshot_service.attach_ltc_summary_fields(
            snapshot,
            ltc_summary=ltc_summary,
        )

    # ------------------------------------------------------------------
    # GET /api/spsa/params
    # ------------------------------------------------------------------
    async def get_params(self, request: web.Request) -> web.Response:
        parsed_query = SpsaParamsQuery.model_validate(dict(request.rel_url.query))
        variant_id = parsed_query.variant_id
        overall_start = time.perf_counter()
        if variant_id:
            entry = self._params_service.load_variant_entry(variant_id)
            total_elapsed = (time.perf_counter() - overall_start) * 1000.0
            if total_elapsed >= 50.0:
                print(
                    f"[spsa:params_total] variant={variant_id} duration_ms={total_elapsed:.1f}",
                    flush=True,
                )
            if entry is not None:
                return web.json_response({"variant": variant_id, "entry": entry})
            return json_error_response("variant not found", status=404, code="variant_not_found")

        payload_start = time.perf_counter()
        payload = self._params_service.build_params_payload()
        payload_elapsed = (time.perf_counter() - payload_start) * 1000.0
        total_elapsed = (time.perf_counter() - overall_start) * 1000.0
        if payload_elapsed >= 50.0:
            print(
                f"[spsa:params_service] duration_ms={payload_elapsed:.1f} num_params={payload.get('num_params')}",
                flush=True,
            )
        if total_elapsed >= 200.0:
            print(f"[spsa:params_total] duration_ms={total_elapsed:.1f}", flush=True)
        return web.json_response(payload)

    # ------------------------------------------------------------------
    # GET /api/spsa/update/{idx}
    # ------------------------------------------------------------------
    async def get_update(self, request: web.Request) -> web.Response:
        try:
            parsed_path = SpsaUpdatePath.model_validate(dict(request.match_info))
        except ValidationError:
            return json_error_response("idx must be int", status=400, code="invalid_index")
        idx = parsed_path.idx
        try:
            detail_view = parse_detail_view_config(request)
        except ValueError:
            return json_error_response("Unsupported detail view", status=400, code="invalid_detail_view")
        try:
            payload = self._update_query_service.build_update_detail(idx)
        except ValueError as exc:
            message = str(exc) or "no events"
            return json_error_response(message, status=404, code="no_events")
        response_payload = apply_detail_view({**payload}, detail_view)
        return web.json_response(response_payload)

    # ------------------------------------------------------------------
    # GET /api/spsa/variants & variant/{id}
    # ------------------------------------------------------------------
    async def get_variants(self, _request: web.Request) -> web.Response:
        variants = self._store.load_variants_map()
        return web.json_response({"variants": list(variants.keys())})

    async def get_variant(self, request: web.Request) -> web.Response:
        variant_id = request.match_info.get("variant_id")
        if not variant_id:
            return json_error_response("variant_id required", status=400, code="missing_variant_id")
        variants = self._store.load_variants_map()
        entry = variants.get(variant_id)
        if entry is not None:
            return web.json_response({"variant": variant_id, "entry": entry})
        return json_error_response("variant not found", status=404, code="variant_not_found")

    # ------------------------------------------------------------------
    # GET /api/spsa/games
    # ------------------------------------------------------------------
    async def get_games(self, request: web.Request) -> web.Response:
        try:
            parsed_query = PaginatedSearchQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        offset = parsed_query.offset
        limit = parsed_query.limit
        search_query = parsed_query.q.strip().lower()

        games, total = self._game_listing_service.list_games(offset=offset, limit=limit, search_query=search_query)
        return web.json_response({"games": games, "total": total, "offset": offset, "limit": limit})

    # ------------------------------------------------------------------
    # GET /api/spsa/game/{game_id}
    # ------------------------------------------------------------------
    async def get_game(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]

        record = self._game_query.load_game_record(self._db_path, game_name=game_id)
        if record is not None:
            game_data = build_game_detail_payload(record=record, game_id=game_id, logger=logger)

            event_meta = self._update_query_service.get_game_event_snapshot(game_id) or {}
            phase_value = event_meta.get("phase")
            update_idx_value = event_meta.get("update_idx")
            resolved_variant = resolve_variant_id(update_idx_value)
            if update_idx_value is None:
                resolved_variant = extract_variant_from_game_id(game_id) or resolved_variant

            black_name = game_data.get("black_player")
            white_name = game_data.get("white_player")
            game_data["black_name"] = coerce_optional_text(black_name) or ""
            game_data["white_name"] = coerce_optional_text(white_name) or ""
            game_data["variant_id"] = resolved_variant
            game_data["phase"] = phase_value if isinstance(phase_value, str) else None

            return web.json_response(game_data)

        return json_error_response("Game not found", status=404, code="game_not_found")

    async def get_ltc_summary(self, _request: web.Request) -> web.Response:
        summary = self._ltc_service.compute_ltc_summary()
        return web.json_response(summary)

    async def get_ltc_results(self, request: web.Request) -> web.Response:
        try:
            parsed_query = LtcResultsQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_limit")
        payload = self._snapshot_service.compose_ltc_results_snapshot(limit=parsed_query.limit)
        return web.json_response(payload)

    # ------------------------------------------------------------------
    # GET /api/spsa/updates
    # ------------------------------------------------------------------
    async def get_updates(self, request: web.Request) -> web.Response:
        try:
            parsed_query = SpsaUpdatesQuery.model_validate(dict(request.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        limit = parsed_query.limit
        offset = parsed_query.offset

        updates = self._update_query_service.load_index_updates()
        if not updates:
            updates = self._update_query_service.collect_updates_from_events()
        progress = self._update_query_service.compute_progress_snapshot(updates)
        if updates:
            updates.sort(key=lambda entry: entry.get("update_idx", 0), reverse=True)
            paginated = updates[offset : offset + limit]
            return web.json_response(
                {
                    "updates": paginated,
                    "total": len(updates),
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + limit < len(updates),
                    "progress": progress,
                }
            )
        return web.json_response(
            {"updates": [], "total": 0, "limit": limit, "offset": offset, "has_more": False, "progress": progress}
        )

    async def sse_convergence(self, request: web.Request) -> web.StreamResponse | web.Response:
        """Serve convergence analysis as SSE or JSON snapshot depending on query params."""

        if request.rel_url.query.get("format", "").lower() == "json":
            snapshot = self._build_convergence_snapshot(request)
            return web.json_response(snapshot)

        return await self._streams.sse_convergence(request)

    def _build_convergence_snapshot(self, request: web.Request | None = None) -> JsonObject:
        snapshot = self._analysis_cache.get_convergence_snapshot()
        ltc_limit = 200
        if request is not None:
            try:
                parsed_query = LtcLimitQuery.model_validate(dict(request.rel_url.query))
                ltc_limit = parsed_query.ltc_limit
            except ValidationError:
                ltc_limit = 200
        return self._snapshot_service.compose_convergence_with_ltc(snapshot, ltc_limit=ltc_limit)

    def notify_summary_snapshot(self, payload: Mapping[str, JsonValue]) -> None:
        """Receive summary snapshots broadcast by the server and publish to SSE clients."""
        enriched = self._attach_ltc_summary_fields(payload)
        self._streams.publish_summary_snapshot(enriched)

    # ------------------------------------------------------------------
    # GET /api/spsa/analysis/correlation
    # ------------------------------------------------------------------
    async def get_correlation(self, _request: web.Request) -> web.Response:
        snapshot = self._analysis_cache.get_correlation_snapshot()
        return web.json_response(snapshot)
