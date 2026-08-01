"""Durable SPSA ledger revision stream."""

from __future__ import annotations

import logging

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.interfaces.boundaries.parsers.spsa_stream import parse_spsa_payload
from shogiarena._core.shared.kernel.json_types import JsonObject

from .handlers import SpsaStreamHandlersMixin

logger = logging.getLogger(__name__)


class SpsaStreams(SpsaStreamHandlersMixin):
    """Serve the durable SPSA ledger revision feed.

    Notes:
        - The revision feed uses the durable ledger revision as both `seq` and SSE `id`.
        - `Last-Event-ID` is diagnostic only; the revision feed explicitly requires REST recovery.
    """

    def __init__(self, update_query_service: DashboardSpsaUpdateQueryPort) -> None:
        self._update_query_service = update_query_service

    async def _prepare_sse_response(
        self,
        response: web.StreamResponse,
        request: web.Request,
        *,
        log_context: str,
    ) -> bool:
        """Prepare SSE response, treating client disconnects as normal completion."""
        try:
            await response.prepare(request)
            return True
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ClientConnectionResetError) as exc:
            logger.debug("%s SSE client disconnected before start: %s", log_context, exc)
            return False

    def _wrap_revision_payload(self, stream_key: str, payload: JsonObject) -> tuple[JsonObject, int]:
        data = payload.get("data")
        revision = data.get("revision") if isinstance(data, dict) else None
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("revision feed payload requires a non-negative ledger revision")
        envelope = dict(payload)
        envelope["stream"] = stream_key
        envelope["seq"] = revision
        parsed = parse_spsa_payload(
            "stream",
            envelope,
            path="spsa.stream[revision]",
        )
        return parsed, revision
