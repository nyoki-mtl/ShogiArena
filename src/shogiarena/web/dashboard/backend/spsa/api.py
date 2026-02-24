"""SPSA-related API handlers for the arena dashboard."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aiohttp import web
from rshogi.core import Board

from shogiarena.db import ShogiRepository
from shogiarena.records.storage.db_store import DBRecordStore
from shogiarena.utils.types.coerce import strict_int
from shogiarena.web.dashboard.backend.http_helpers import json_error_response
from shogiarena.web.dashboard.backend.live.schema import build_live_view_snapshot
from shogiarena.web.dashboard.backend.tournament.api import TournamentAPI

from .data_service import SpsaDataService
from .detail_view import apply_detail_view, parse_detail_view_config
from .ltc_service import SpsaLtcService
from .params_service import SpsaParamsService
from .store import SpsaStore
from .streams import SpsaStreams
from .summary_service import SpsaSummaryService
from .types import ConvergenceAnalysis, UpdateEntry
from .utils import (
    extract_variant_from_game_id,
    resolve_variant_id,
)

logger = logging.getLogger(__name__)


class SPSAAPI:
    """Expose SPSA endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path | None,
        ensure_shogidb: Callable[[], None],
        shogidb_supplier: Callable[[], ShogiRepository | None],
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        self._ensure_shogidb = ensure_shogidb
        self._get_shogidb = shogidb_supplier
        self._store = SpsaStore(
            run_dir=run_dir,
            db_path=db_path,
            ensure_shogidb=ensure_shogidb,
            shogidb_supplier=shogidb_supplier,
        )
        self._data_service = SpsaDataService(
            store=self._store,
            ensure_shogidb=ensure_shogidb,
            shogidb_supplier=shogidb_supplier,
        )
        self._summary_service = SpsaSummaryService(store=self._store)
        self._ltc_service = SpsaLtcService(store=self._store)
        self._streams = SpsaStreams(
            data_service=self._data_service,
            store=self._store,
            ltc_service=self._ltc_service,
            summary_supplier=self._build_summary_payload,
        )
        self._params_service = SpsaParamsService(store=self._store, run_dir=run_dir)

    # ------------------------------------------------------------------
    # Route registration helpers
    # ------------------------------------------------------------------
    def register_routes(self, app: web.Application) -> None:
        """Register SPSA endpoints on the given aiohttp application."""
        app.router.add_get("/api/spsa/events", self.get_spsa_events)
        app.router.add_get("/api/spsa/summary", self.get_spsa_summary)
        app.router.add_get("/api/spsa/params", self.get_spsa_params)
        app.router.add_get("/api/spsa/update/{idx}", self.get_spsa_update)
        app.router.add_get("/api/spsa/updates", self.get_spsa_updates)
        app.router.add_get("/api/spsa/updates/stream", self.sse_spsa_updates)
        app.router.add_get("/api/spsa/update/detail/stream", self.sse_spsa_update_detail)
        app.router.add_get("/api/spsa/variants", self.get_spsa_variants)
        app.router.add_get("/api/spsa/variant/{variant_id}", self.get_spsa_variant)
        app.router.add_get("/api/spsa/variant/games/stream", self.sse_spsa_variant_games)
        app.router.add_get("/ws/spsa/updates", self.websocket_spsa_updates)
        app.router.add_get("/api/spsa/analysis/correlation", self.get_spsa_correlation)
        app.router.add_get("/api/spsa/analysis/correlation/stream", self.sse_spsa_correlation)
        app.router.add_get("/api/spsa/analysis/convergence", self.sse_spsa_convergence)
        app.router.add_get("/api/spsa/games", self.get_spsa_games)
        app.router.add_get("/api/spsa/game/{game_id}", self.get_spsa_game)
        app.router.add_get("/api/spsa/ltc/summary", self.get_ltc_summary)
        app.router.add_get("/api/spsa/ltc/results", self.get_ltc_results)
        app.router.add_get("/api/spsa/ltc/results/stream", self.sse_ltc_results)
        app.router.add_get("/api/spsa/ltc/progress/stream", self.sse_ltc_progress)
        app.router.add_get("/api/spsa/ltc/games/stream", self.sse_ltc_games)
        app.router.add_get("/api/spsa/summary/stream", self.sse_spsa_summary)

    # ------------------------------------------------------------------
    # GET /api/spsa/events
    # ------------------------------------------------------------------
    async def get_spsa_events(self, request: web.Request) -> web.Response:
        limit = int(request.rel_url.query.get("limit", 200))
        limit = max(1, min(limit, 5000))
        event_entries = self._store.load_event_entries()
        if not event_entries:
            return web.json_response({"events": []})

        events: list[dict[str, Any]] = []
        for event_data in event_entries[-limit:]:
            event_base = {k: v for k, v in event_data.items() if k != "payload"}
            payload = dict(event_base)
            event_base["type"] = event_data.get("event", "event")
            event_base["timestamp"] = event_base.get("ts", int(time.time() * 1000))
            event_base["payload"] = payload
            events.append(event_base)

        return web.json_response({"events": events})

    # ------------------------------------------------------------------
    # GET /api/spsa/summary
    # ------------------------------------------------------------------
    async def get_spsa_summary(self, request: web.Request) -> web.Response:
        summary_payload = self._build_summary_payload()
        return web.json_response(summary_payload)

    def _build_summary_payload(self) -> dict[str, Any]:
        overall_start = time.perf_counter()
        ltc_start = time.perf_counter()
        ltc_summary = self._ltc_service.compute_ltc_summary()
        ltc_elapsed = (time.perf_counter() - ltc_start) * 1000.0
        if ltc_elapsed >= 50.0:
            print(f"[spsa:ltc_summary] duration_ms={ltc_elapsed:.1f}", flush=True)

        summary_start = time.perf_counter()
        summary_data = dict(self._summary_service.compute_summary())
        summary_elapsed = (time.perf_counter() - summary_start) * 1000.0
        if summary_elapsed >= 50.0:
            print(
                f"[spsa:summary_service] duration_ms={summary_elapsed:.1f} "
                f"completed={summary_data.get('games', {}).get('completed')} "
                f"total={summary_data.get('games', {}).get('total')}",
                flush=True,
            )

        summary_data = self._attach_ltc_summary_fields(summary_data, ltc_summary=ltc_summary)
        summary_data.setdefault("mode", "spsa")
        summary_data.setdefault("summarySource", "spsa")
        summary_data["liveView"] = build_live_view_snapshot(summary_data)
        total_elapsed = (time.perf_counter() - overall_start) * 1000.0
        if total_elapsed >= 200.0:
            print(
                f"[spsa:summary_total] duration_ms={total_elapsed:.1f}",
                flush=True,
            )
        return summary_data

    def _attach_ltc_summary_fields(
        self,
        snapshot: Mapping[str, Any],
        *,
        ltc_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge LTC regression info into a summary snapshot."""

        enriched = dict(snapshot)
        summary_payload: Mapping[str, Any] | None = ltc_summary
        if summary_payload is None:
            summary_payload = self._ltc_service.compute_ltc_summary()

        if isinstance(summary_payload, Mapping):
            summary_dict = dict(summary_payload)
            enriched["ltc_regression"] = summary_dict
            ltc_elo = summary_dict.get("elo")
            enriched["elo"] = ltc_elo
            enriched["btd_elo"] = ltc_elo
        else:
            enriched.setdefault("ltc_regression", None)
            enriched.setdefault("elo", None)
            enriched.setdefault("btd_elo", None)

        enriched.setdefault("btd_se", None)
        enriched.setdefault("btd_los", None)
        return enriched

    # ------------------------------------------------------------------
    # GET /api/spsa/params
    # ------------------------------------------------------------------
    async def get_spsa_params(self, request: web.Request) -> web.Response:
        variant_id = request.rel_url.query.get("variant_id")
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
    async def get_spsa_update(self, request: web.Request) -> web.Response:
        idx_str = request.match_info.get("idx")
        if not idx_str or not idx_str.isdigit():
            return json_error_response("idx must be int", status=400, code="invalid_index")
        idx = int(idx_str)
        try:
            detail_view = parse_detail_view_config(request)
        except ValueError:
            return json_error_response("Unsupported detail view", status=400, code="invalid_detail_view")
        try:
            payload = self._data_service.build_update_detail(idx)
        except ValueError as exc:
            message = str(exc) or "no events"
            return json_error_response(message, status=404, code="no_events")
        response_payload = apply_detail_view({**payload}, detail_view)
        return web.json_response(response_payload)

    # ------------------------------------------------------------------
    # GET /api/spsa/variants & variant/{id}
    # ------------------------------------------------------------------
    async def get_spsa_variants(self, request: web.Request) -> web.Response:
        variants = self._store.load_variants_map()
        return web.json_response({"variants": list(variants.keys())})

    async def get_spsa_variant(self, request: web.Request) -> web.Response:
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
    async def get_spsa_games(self, request: web.Request) -> web.Response:
        offset = max(int(request.rel_url.query.get("offset", 0) or 0), 0)
        limit = min(max(int(request.rel_url.query.get("limit", 100) or 100), 1), 1000)
        search_raw = request.rel_url.query.get("q", "") or ""
        search_query = search_raw.strip().lower()

        games, total = self._data_service.list_games(offset=offset, limit=limit, search_query=search_query)
        return web.json_response({"games": games, "total": total, "offset": offset, "limit": limit})

    # ------------------------------------------------------------------
    # GET /api/spsa/game/{game_id}
    # ------------------------------------------------------------------
    async def get_spsa_game(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]

        self._ensure_shogidb()
        shogidb = self._get_shogidb()
        if shogidb is not None:
            record_store = DBRecordStore(shogidb)
            record = record_store.load(game_name=game_id)
            if record is None:
                return json_error_response("Game not found", status=404, code="game_not_found")
            metadata = record.metadata
            game_id_value = record.game_name or game_id
            black_player = metadata.black_player or ""
            white_player = metadata.white_player or ""
            black_tc = record.black_time_control
            white_tc = record.white_time_control
            tc_black = black_tc.to_spec() if black_tc is not None else None
            tc_white = white_tc.to_spec() if white_tc is not None else None
            start_time = metadata.start_date
            end_time = metadata.end_date

            initial_sfen_raw = record.init_position_sfen
            initial_sfen = initial_sfen_raw if initial_sfen_raw != "startpos" else None
            board = Board()
            if initial_sfen:
                board.set_sfen(initial_sfen)
            moves_usi: list[str] = []
            moves_ki2: list[str] = []
            eval_values: list[int | None] = []
            nodes_values: list[int | None] = []
            depth_values: list[int | None] = []
            seldepth_values: list[int | None] = []
            move_times_ms: list[int | None] = []
            wall_times_ms: list[int | None] = []
            latency_deltas_ms: list[int | None] = []
            for move_entry in record.moves:
                mv = move_entry.move
                move_time = move_entry.time_ms
                engine_info = move_entry.engine_info
                move_text = mv.to_usi()
                if not board.is_legal_move(mv):
                    logger.error("Illegal move: %s", move_text)
                    break
                moves_ki2.append(board.move32_from_move(mv).to_ki2(board) or mv.to_usi())
                moves_usi.append(move_text)
                board.apply_move(mv)

                eval_cp = engine_info.eval if engine_info is not None else None
                nodes = engine_info.nodes if engine_info is not None else None
                depth = engine_info.depth if engine_info is not None else None
                seldepth = engine_info.seldepth if engine_info is not None else None
                wall_time = engine_info.wall_time_ms if engine_info is not None else None
                latency_delta = engine_info.latency_delta_ms if engine_info is not None else None
                move_time_value = strict_int(move_time)
                eval_value = strict_int(eval_cp)
                nodes_value = strict_int(nodes)
                depth_value = strict_int(depth)
                seldepth_values.append(strict_int(seldepth))
                wall_time_value = strict_int(wall_time)
                latency_deltas_ms.append(strict_int(latency_delta))
                move_times_ms.append(move_time_value)
                eval_values.append(eval_value)
                nodes_values.append(nodes_value)
                depth_values.append(depth_value)
                wall_times_ms.append(wall_time_value)

            if not any(value is not None for value in move_times_ms):
                move_times_ms = []
            if not any(value is not None for value in wall_times_ms):
                wall_times_ms = []
            if not any(value is not None for value in latency_deltas_ms):
                latency_deltas_ms = []

            eval_black, eval_white = TournamentAPI._compute_eval_arrays(
                eval_values,
                initial_sfen_raw,
                len(moves_usi),
            )

            event_meta = self._data_service.get_game_event_snapshot(game_id) or {}
            phase_value = event_meta.get("phase")
            update_idx_value = event_meta.get("update_idx")
            resolved_variant = resolve_variant_id(update_idx_value)
            if update_idx_value is None:
                resolved_variant = extract_variant_from_game_id(game_id) or resolved_variant

            result_obj = record.result
            game_data = {
                "game_id": game_id_value,
                "black_player": black_player,
                "white_player": white_player,
                "black_name": black_player,
                "white_name": white_player,
                "result_code": result_obj.value if result_obj is not None else None,
                "initial_sfen": initial_sfen_raw,
                "time_control_black": tc_black,
                "time_control_white": tc_white,
                "moves": moves_usi,
                "ki2_moves": moves_ki2,
                "eval_black": eval_black,
                "eval_white": eval_white,
                "nodes_values": nodes_values,
                "depth_values": depth_values,
                "seldepth_values": seldepth_values,
                "move_times_ms": move_times_ms,
                "wall_times_ms": wall_times_ms,
                "latency_deltas_ms": latency_deltas_ms,
                "total_plies": len(record.moves),
                "start_time": start_time,
                "end_time": end_time,
                "variant_id": resolved_variant,
                "phase": phase_value if isinstance(phase_value, str) else None,
            }

            return web.json_response(game_data)

        return json_error_response("Game not found", status=404, code="game_not_found")

    async def get_ltc_summary(self, request: web.Request) -> web.Response:
        summary = self._ltc_service.compute_ltc_summary()
        return web.json_response(summary)

    async def get_ltc_results(self, request: web.Request) -> web.Response:
        limit_raw = request.rel_url.query.get("limit", 50)
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))

        results = self._store.load_ltc_results()
        enriched_results = self._ltc_service.enrich_ltc_results(results)
        total = len(enriched_results)
        subset = enriched_results[-limit:] if limit < total else enriched_results
        subset_out = list(reversed(subset))

        summary = self._ltc_service.compute_ltc_summary(enriched_results=enriched_results)

        payload = {
            "total": total,
            "results": subset_out,
            "summary": summary,
        }
        return web.json_response(payload)

    # ------------------------------------------------------------------
    # GET /api/spsa/updates
    # ------------------------------------------------------------------
    async def get_spsa_updates(self, request: web.Request) -> web.Response:
        limit = min(int(request.query.get("limit", 50)), 1000)
        offset = max(int(request.query.get("offset", 0)), 0)

        updates = self._data_service.load_index_updates()
        if not updates:
            updates = self._data_service.collect_updates_from_events()
        progress = self._data_service.compute_progress_snapshot(updates)
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

    # ------------------------------------------------------------------
    # GET /api/spsa/variant/games/stream
    # ------------------------------------------------------------------
    async def sse_spsa_variant_games(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for variant games stream - delegates to streams service."""
        return await self._streams.sse_variant_games(request)

    async def sse_spsa_updates(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for updates stream - delegates to streams service."""
        return await self._streams.sse_updates(request)

    async def sse_spsa_update_detail(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for detailed update payloads - delegates to streams service."""
        return await self._streams.sse_update_detail(request)

    async def sse_spsa_convergence(self, request: web.Request) -> web.StreamResponse | web.Response:
        """Serve convergence analysis as SSE or JSON snapshot depending on query params."""

        if request.rel_url.query.get("format", "").lower() == "json":
            snapshot = self._build_convergence_snapshot(request)
            return web.json_response(snapshot)

        return await self._streams.sse_convergence(request)

    def _build_convergence_snapshot(self, request: web.Request | None = None) -> ConvergenceAnalysis:
        updates = self._data_service.load_index_updates()
        if len(updates) < 2:
            updates = self._data_service.collect_updates_from_events()
        payload = self._data_service.compute_convergence_analysis(updates)
        ltc_snapshot = self._build_ltc_results_snapshot(request)
        if ltc_snapshot is not None:
            payload["ltc_results"] = ltc_snapshot
        return payload

    def _build_ltc_results_snapshot(self, request: web.Request | None = None) -> dict[str, Any] | None:
        if self._ltc_service is None:
            return None
        raw_results = self._store.load_ltc_results()
        enriched_results = self._ltc_service.enrich_ltc_results(raw_results)
        total = len(enriched_results)
        limit = 200
        if request is not None:
            try:
                limit = int(request.rel_url.query.get("ltc_limit", limit))
            except (TypeError, ValueError):
                limit = 200
        limit = max(1, min(limit, 500))
        subset = enriched_results[-limit:] if limit < total else enriched_results
        subset_out = list(reversed(subset))
        summary = self._ltc_service.compute_ltc_summary(enriched_results=enriched_results)
        return {
            "total": total,
            "results": subset_out,
            "summary": summary,
        }

    async def sse_ltc_results(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for LTC results stream - delegates to streams service."""
        return await self._streams.sse_ltc_results(request)

    async def sse_ltc_progress(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for LTC progress stream - delegates to streams service."""
        return await self._streams.sse_ltc_progress(request)

    async def sse_ltc_games(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for LTC games stream - delegates to streams service."""
        return await self._streams.sse_ltc_games(request)

    async def sse_spsa_summary(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for SPSA summary stream - delegates to streams service."""
        return await self._streams.sse_summary(request)

    async def sse_spsa_correlation(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for correlation analysis stream - delegates to streams service."""
        return await self._streams.sse_correlation(request)

    def notify_summary_snapshot(self, payload: Mapping[str, Any]) -> None:
        """Receive summary snapshots broadcast by the server and publish to SSE clients."""
        enriched = self._attach_ltc_summary_fields(payload)
        self._streams.publish_summary_snapshot(enriched)

    # ------------------------------------------------------------------
    # GET /ws/spsa/updates
    # ------------------------------------------------------------------
    async def websocket_spsa_updates(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint for updates - delegates to streams service."""
        return await self._streams.websocket_updates(request)

    # ------------------------------------------------------------------
    # GET /api/spsa/analysis/correlation
    # ------------------------------------------------------------------
    async def get_spsa_correlation(self, request: web.Request) -> web.Response:
        updates = self._data_service.load_index_updates()
        # Correlation analysis requires at least 2 updates; fall back to events if insufficient
        if len(updates) < 2:
            updates = self._data_service.collect_updates_from_events()
        result = self._data_service.compute_correlation_analysis(updates)
        return web.json_response(result)

    def _collect_updates_from_events(self) -> list[UpdateEntry]:
        """Expose raw event-derived updates (used by diagnostics and tests)."""
        return self._data_service.collect_updates_from_events()
