"""Handler facade for the durable SPSA revision feed."""

from __future__ import annotations

from aiohttp import web

from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject

from .revision_handlers import sse_revisions


class SpsaStreamHandlersMixin:
    """Expose the single durable SPSA stream endpoint."""

    _update_query_service: DashboardSpsaUpdateQueryPort

    async def _prepare_sse_response(
        self,
        response: web.StreamResponse,
        request: web.Request,
        *,
        log_context: str,
    ) -> bool:
        raise NotImplementedError

    def _wrap_revision_payload(self, stream_key: str, payload: JsonObject) -> tuple[JsonObject, int]:
        raise NotImplementedError

    async def sse_revisions(self, request: web.Request) -> web.StreamResponse:
        return await sse_revisions(self, request)
