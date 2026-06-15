"""SPSA streaming handlers for SSE and WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from shogiarena._core.contexts.dashboard.application.spsa.analysis_cache import AnalysisCacheService
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import UpdateEntry
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import (
    DashboardSpsaAnalysisPort,
    DashboardSpsaLtcServicePort,
    DashboardSpsaStorePort,
    DashboardSpsaUpdateQueryPort,
)
from shogiarena._core.contexts.spsa.application.dashboard.snapshot_composition import SpsaSnapshotCompositionService
from shogiarena._core.interfaces.boundaries.parsers.spsa_stream import parse_spsa_payload
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .handlers import SpsaStreamHandlersMixin

logger = logging.getLogger(__name__)


class SpsaStreams(SpsaStreamHandlersMixin):
    """Streaming handlers for SPSA SSE and WebSocket endpoints.

    Notes:
        - SSE events are wrapped with monotonically increasing `seq` and the SSE `id` field is set.
        - `Last-Event-ID` is accepted and echoed as `resume_from` for diagnostics; no replay is performed.
    """

    def __init__(
        self,
        update_query_service: DashboardSpsaUpdateQueryPort,
        *,
        analysis_service: DashboardSpsaAnalysisPort | None = None,
        store: DashboardSpsaStorePort | None = None,
        ltc_service: DashboardSpsaLtcServicePort | None = None,
        snapshot_service: SpsaSnapshotCompositionService | None = None,
        summary_supplier: Callable[[], JsonObject] | None = None,
        analysis_cache: AnalysisCacheService | None = None,
    ) -> None:
        self._update_query_service = update_query_service
        self._analysis_service = analysis_service
        self._store = store
        self._ltc_service = ltc_service
        self._snapshot_service = snapshot_service
        self._analysis_cache = analysis_cache
        self._summary_supplier = summary_supplier
        self._summary_condition: asyncio.Condition = asyncio.Condition()
        self._latest_summary: JsonObject | None = None
        self._detail_metric_name = "spsa:hydration:update-detail"
        self._sse_seq: dict[str, int] = {}

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

    def _progress_payload(self, updates: Sequence[UpdateEntry] | None = None) -> JsonObject:
        snapshot = self._update_query_service.compute_progress_snapshot(updates or [])
        completed = snapshot.get("completed") or 0
        total = snapshot.get("total")
        percent = snapshot.get("percent")
        return {
            "completed": int(completed),
            "total": int(total) if isinstance(total, int | float) else None,
            "percent": float(percent) if isinstance(percent, int | float) else None,
        }

    def _next_sse_seq(self, stream_key: str) -> int:
        current = self._sse_seq.get(stream_key, 0) + 1
        self._sse_seq[stream_key] = current
        return current

    def _wrap_sse_payload(self, stream_key: str, payload: JsonObject) -> tuple[JsonObject, int]:
        seq = self._next_sse_seq(stream_key)
        envelope = dict(payload)
        envelope.setdefault("stream", stream_key)
        envelope["seq"] = seq
        parsed = parse_spsa_payload(
            "stream",
            envelope,
            path=f"spsa.stream[{stream_key}]",
        )
        return parsed, seq

    @staticmethod
    def _signature_for_detail(detail: Mapping[str, JsonValue]) -> str:
        """Generate a signature string for change detection.

        Raises:
            TypeError: If detail contains non-serializable values.
        """
        return json.dumps(detail, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _measure_payload_kb(payload: Mapping[str, JsonValue]) -> float:
        """Measure payload size in KB for diagnostics.

        Raises:
            TypeError: If payload contains non-serializable values.
        """
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return len(encoded) / 1024.0

    def _record_detail_metric(self, payload: Mapping[str, JsonValue], *, view: str, include_count: int) -> None:
        size_kb = self._measure_payload_kb(payload)
        if size_kb <= 0.0:
            return
        metric = {
            "metric": self._detail_metric_name,
            "view": view,
            "include_count": include_count,
            "payload_kb": round(size_kb, 2),
        }
        if view == "slim":
            metric["detail_payload_kb_slim"] = metric["payload_kb"]
        else:
            metric["detail_payload_kb_full"] = metric["payload_kb"]
        logger.debug("[detail-stream] %s", metric)
