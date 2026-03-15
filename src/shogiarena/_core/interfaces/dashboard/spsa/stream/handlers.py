"""Composable handlers for SPSA streaming endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence

from aiohttp import web

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import UpdateEntry
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaAnalysisPort,
    DashboardSpsaLtcServicePort,
    DashboardSpsaStorePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.application.dashboard.snapshot_composition import SpsaSnapshotCompositionService
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .analysis_handlers import sse_convergence, sse_correlation
from .summary_handlers import (
    publish_summary_snapshot,
    sse_ltc_progress,
    sse_ltc_results,
    sse_summary,
)
from .updates_handlers import sse_update_detail, sse_updates, websocket_updates
from .variant_handlers import resolve_variant_stream_targets, sse_ltc_games, sse_variant_games


class SpsaStreamHandlersMixin:
    """Endpoint methods for SPSA streaming APIs."""

    _update_query_service: DashboardSpsaUpdateQueryPort
    _analysis_service: DashboardSpsaAnalysisPort | None
    _store: DashboardSpsaStorePort | None
    _ltc_service: DashboardSpsaLtcServicePort | None
    _snapshot_service: SpsaSnapshotCompositionService | None
    _summary_supplier: Callable[[], JsonObject] | None
    _summary_condition: asyncio.Condition
    _latest_summary: JsonObject | None

    async def _prepare_sse_response(
        self,
        response: web.StreamResponse,
        request: web.Request,
        *,
        log_context: str,
    ) -> bool:
        raise NotImplementedError

    def _progress_payload(self, updates: Sequence[UpdateEntry] | None = None) -> JsonObject:
        raise NotImplementedError

    def _wrap_sse_payload(self, stream_key: str, payload: JsonObject) -> tuple[JsonObject, int]:
        raise NotImplementedError

    @staticmethod
    def _signature_for_detail(detail: Mapping[str, JsonValue]) -> str:
        raise NotImplementedError

    def _record_detail_metric(self, payload: Mapping[str, JsonValue], *, view: str, include_count: int) -> None:
        raise NotImplementedError

    def resolve_variant_stream_targets(self, limit: int) -> list[int]:
        return resolve_variant_stream_targets(self, limit)

    async def sse_variant_games(self, request: web.Request) -> web.StreamResponse:
        return await sse_variant_games(self, request)

    async def sse_ltc_games(self, request: web.Request) -> web.StreamResponse:
        return await sse_ltc_games(self, request)

    async def sse_update_detail(self, request: web.Request) -> web.StreamResponse:
        return await sse_update_detail(self, request)

    async def sse_updates(self, request: web.Request) -> web.StreamResponse:
        return await sse_updates(self, request)

    async def websocket_updates(self, request: web.Request) -> web.WebSocketResponse:
        return await websocket_updates(self, request)

    async def sse_correlation(self, request: web.Request) -> web.StreamResponse:
        return await sse_correlation(self, request)

    async def sse_convergence(self, request: web.Request) -> web.StreamResponse:
        return await sse_convergence(self, request)

    def publish_summary_snapshot(self, payload: Mapping[str, JsonValue]) -> None:
        publish_summary_snapshot(self, payload)

    async def sse_summary(self, request: web.Request) -> web.StreamResponse:
        return await sse_summary(self, request)

    async def sse_ltc_results(self, request: web.Request) -> web.StreamResponse:
        return await sse_ltc_results(self, request)

    async def sse_ltc_progress(self, request: web.Request) -> web.StreamResponse:
        return await sse_ltc_progress(self, request)
