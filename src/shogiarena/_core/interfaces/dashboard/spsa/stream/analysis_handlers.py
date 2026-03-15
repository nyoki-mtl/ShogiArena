"""Correlation/convergence stream endpoint handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse_runtime import prepare_sse_runtime
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

from .query_models import ConvergenceStreamQuery, UpdatesStreamQuery

logger = logging.getLogger(__name__)


async def sse_correlation(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming correlation analysis snapshots."""
    try:
        parsed_query = UpdatesStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_correlation",
        stream_key="correlation",
        event_name="spsa_correlation",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    poll_interval = parsed_query.poll_interval
    poll_interval = max(0.5, min(poll_interval, 10.0))
    should_send_initial = parsed_query.should_send_initial
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signature: str | None = None

    try:
        while True:
            if handler._analysis_cache is not None:
                result = handler._analysis_cache.get_correlation_snapshot()
            else:
                updates = handler._update_query_service.load_index_updates()
                if len(updates) < 2:
                    updates = handler._update_query_service.collect_updates_from_events()
                result = handler._analysis_service.compute_correlation_analysis(updates)
                result["status"] = "ready"
                result["updated_at"] = int(time.time() * 1000)
            signature = json.dumps(result, sort_keys=True, ensure_ascii=False)

            if should_send_initial or signature != last_signature:
                correlation_payload: JsonObject = {
                    "type": "correlation_update",
                    "data": result,
                    "timestamp": int(time.time() * 1000),
                }
                await runtime.push_event(correlation_payload)
                last_signature = signature
                should_send_initial = False

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA correlation SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA correlation SSE response")

    return response


async def sse_convergence(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming convergence analysis."""
    try:
        parsed_query = ConvergenceStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_convergence",
        stream_key="convergence",
        event_name="spsa_convergence",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    poll_interval = parsed_query.poll_interval
    poll_interval = max(0.5, min(poll_interval, 10.0))
    should_send_initial = parsed_query.should_send_initial
    ltc_limit = parsed_query.ltc_limit
    ltc_limit = max(1, min(ltc_limit, 500))
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signature: str | None = None

    try:
        while True:
            if handler._analysis_cache is not None:
                result = handler._analysis_cache.get_convergence_snapshot()
            else:
                updates = handler._update_query_service.load_index_updates()
                if not updates:
                    updates = handler._update_query_service.collect_updates_from_events()
                result = handler._analysis_service.compute_convergence_analysis(updates)
                result["status"] = "ready"
                result["updated_at"] = int(time.time() * 1000)
            ltc_signature: JsonObject | None = None
            if handler._snapshot_service is not None:
                result = handler._snapshot_service.compose_convergence_with_ltc(result, ltc_limit=ltc_limit)
                ltc_results_data = result.get("ltc_results")
                if isinstance(ltc_results_data, dict):
                    summary = ltc_results_data.get("summary")
                    summary_status = summary.get("status") if isinstance(summary, dict) else None
                    latest = summary.get("latest") if isinstance(summary, dict) else None
                    latest_update = latest.get("update_idx") if isinstance(latest, dict) else None
                    ltc_signature = {
                        "total": ltc_results_data.get("total"),
                        "status": summary_status,
                        "latest_update": latest_update,
                    }

            # Build signature from key fields for change detection
            mobility_signature: JsonObject | None = None
            mobility_series = result.get("mobility_series")
            if isinstance(mobility_series, dict):
                gain_series = mobility_series.get("gain_ak")
                variant_indices = mobility_series.get("variant_indices")
                if isinstance(gain_series, list) and isinstance(variant_indices, list):
                    last_gain = None
                    for value in reversed(gain_series):
                        if isinstance(value, int | float):
                            last_gain = value
                            break
                    last_variant = None
                    for value in reversed(variant_indices):
                        if isinstance(value, int | float):
                            last_variant = coerce_int(value)
                            break
                    mobility_signature = {
                        "count": len(gain_series),
                        "last_gain_ak": last_gain,
                        "last_variant": last_variant,
                    }
            signature_parts = {
                "delta_norm_history": result.get("delta_norm_history"),
                "delta_mean_vector_norm_history": result.get("delta_mean_vector_norm_history"),
                "available_updates": result.get("available_updates"),
                "pending_updates": result.get("pending_updates"),
                "convergence_metrics": result.get("convergence_metrics"),
                "prediction": result.get("prediction"),
                "ltc_signature": ltc_signature,
                "mobility_signature": mobility_signature,
            }
            signature = json.dumps(signature_parts, sort_keys=True)

            if should_send_initial or signature != last_signature:
                convergence_payload: JsonObject = {
                    "type": "convergence_update",
                    "data": result,
                    "timestamp": int(time.time() * 1000),
                }
                await runtime.push_event(convergence_payload)
                last_signature = signature
                should_send_initial = False

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA convergence SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA convergence SSE response")

    return response
