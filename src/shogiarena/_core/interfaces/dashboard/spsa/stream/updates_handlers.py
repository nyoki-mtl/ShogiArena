"""Update/detail/WebSocket stream endpoint handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiohttp import WSMsgType, web
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.application.spsa.detail_payload_filter import (
    apply_detail_view,
    parse_detail_view_config,
)
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import UpdateEntry
from shogiarena._core.interfaces.boundaries.parsers.spsa_stream import parse_spsa_payload
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse_runtime import prepare_sse_runtime
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject

from .query_models import (
    SpsaWebSocketClientMessage,
    TargetsStreamQuery,
    UpdatesStreamQuery,
    WebSocketQuery,
    resolve_targets_query,
)

logger = logging.getLogger(__name__)


async def sse_update_detail(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint that streams filtered SPSA update detail payloads."""

    try:
        detail_view = parse_detail_view_config(request)
    except ValueError:
        return json_error_response("unsupported detail view", status=400, code="invalid_detail_view")

    MAX_TARGETS = 100
    try:
        parsed_query = TargetsStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")
    try:
        resolved_query = resolve_targets_query(
            parsed_query,
            default_limit=10,
            max_targets=MAX_TARGETS,
            resolve_dynamic_targets=handler.resolve_variant_stream_targets,
        )
    except ValueError as exc:
        return json_error_response(str(exc), status=400, code="invalid_index")
    targets = resolved_query.targets
    has_dynamic_targets = resolved_query.has_dynamic_targets
    target_limit = resolved_query.target_limit

    if not targets:
        return json_error_response("update detail unavailable", status=404, code="no_updates")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_update_detail",
        stream_key="update_detail",
        event_name="spsa_update_detail",
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
    last_signatures: dict[int, str | None] = dict.fromkeys(targets)
    include_count = len(detail_view.includes)

    try:
        while True:
            current_targets = targets
            if has_dynamic_targets:
                refreshed = handler.resolve_variant_stream_targets(target_limit)
                if refreshed:
                    current_targets = refreshed
                    targets = refreshed
                    last_signatures = {idx: last_signatures.get(idx) for idx in current_targets}

            batch: list[JsonObject] = []
            for target_idx in current_targets:
                try:
                    detail = handler._update_query_service.build_update_detail(target_idx)
                except ValueError as exc:
                    logger.debug("Skipping update detail for idx=%s: %s", target_idx, exc)
                    continue
                filtered = apply_detail_view(to_json_object(detail), detail_view)
                signature = handler._signature_for_detail(filtered)
                if should_send_initial or signature != last_signatures.get(target_idx):
                    batch.append(filtered)
                    last_signatures[target_idx] = signature
                    handler._record_detail_metric(
                        filtered,
                        view=detail_view.view,
                        include_count=include_count,
                    )

            if should_send_initial or batch:
                await runtime.push_event(
                    {
                        "type": "detail_batch",
                        "details": batch,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                should_send_initial = False

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA detail SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA detail SSE response")

    return response


async def sse_updates(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming SPSA updates."""
    try:
        parsed_query = UpdatesStreamQuery.model_validate(dict(request.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_updates",
        stream_key="updates",
        event_name="spsa_updates",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    should_send_initial = parsed_query.should_send_initial
    poll_interval = parsed_query.poll_interval
    poll_interval = max(0.5, min(poll_interval, 10.0))
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signatures: dict[int, str] = {}

    def signature(entry: UpdateEntry) -> str:
        payload = {
            "is_pending": entry.get("is_pending"),
            "ended_at": entry.get("ended_at"),
            "delta_norm": entry.get("delta_norm"),
            "wins": entry.get("wins"),
            "losses": entry.get("losses"),
            "draws": entry.get("draws"),
            "phase_wdl": entry.get("phase_wdl"),
            "ltc_regression": entry.get("ltc_regression"),
            "has_ltc_regression": entry.get("has_ltc_regression"),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    try:
        if should_send_initial:
            updates = handler._update_query_service.load_index_updates()
            if updates:
                limited = updates[-20:]
                for entry in updates:
                    idx_value = entry.get("update_idx")
                    if isinstance(idx_value, int):
                        last_signatures[idx_value] = signature(entry)
                payload = {
                    "type": "initial",
                    "updates": limited,
                    "total": len(updates),
                    "timestamp": int(time.time() * 1000),
                    "progress": handler._progress_payload(updates),
                }
                await runtime.push_event(payload)

        while True:
            updates = handler._update_query_service.load_index_updates()
            if handler._analysis_cache is not None:
                handler._analysis_cache.notify_updates_changed()
            if updates:
                changed: list[UpdateEntry] = []
                for entry in updates:
                    idx_value = entry.get("update_idx")
                    if not isinstance(idx_value, int):
                        continue
                    current_sig = signature(entry)
                    if last_signatures.get(idx_value) != current_sig:
                        last_signatures[idx_value] = current_sig
                        changed.append(entry)
                if changed:
                    payload = {
                        "type": "update",
                        "updates": changed,
                        "timestamp": int(time.time() * 1000),
                        "progress": handler._progress_payload(updates),
                    }
                    await runtime.push_event(payload)

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA updates SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA updates SSE response")

    return response


async def websocket_updates(handler, request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint for SPSA updates."""
    try:
        parsed_query = WebSocketQuery.model_validate(dict(request.query))
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    last_update_idx = parsed_query.last_update_idx

    if parsed_query.should_send_initial:
        updates = handler._update_query_service.load_index_updates()
        if updates:
            new_updates = [entry for entry in updates if entry.get("update_idx", 0) > last_update_idx]
            payload = parse_spsa_payload(
                "websocket",
                {
                    "type": "initial",
                    "updates": new_updates[-20:],
                    "total": len(updates),
                },
                path="spsa.websocket.initial",
            )
            await ws.send_str(json.dumps(payload, ensure_ascii=False))

    last_check = 0.0
    check_interval = 2.0

    async for message in ws:
        if message.type == WSMsgType.TEXT:
            try:
                payload_raw = json.loads(message.data)
                payload = SpsaWebSocketClientMessage.model_validate(payload_raw)
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.debug("Skipping invalid SPSA updates WebSocket message: %s", exc)
                continue
            if payload.type == "ping":
                await ws.send_str(json.dumps({"type": "pong"}))
            elif payload.type == "subscribe_updates":
                if payload.last_update_idx is not None:
                    last_update_idx = payload.last_update_idx
        elif message.type == WSMsgType.ERROR:
            logger.warning("WebSocket error: %s", ws.exception())
            break

        current_time = time.time()
        if current_time - last_check > check_interval:
            last_check = current_time
            updates = handler._update_query_service.load_index_updates()
            if updates:
                new_updates = [entry for entry in updates if entry.get("update_idx", 0) > last_update_idx]
                if new_updates:
                    payload = parse_spsa_payload(
                        "websocket",
                        {
                            "type": "update",
                            "updates": new_updates,
                            "timestamp": int(time.time() * 1000),
                        },
                        path="spsa.websocket.update",
                    )
                    await ws.send_str(json.dumps(payload, ensure_ascii=False))
                    last_update_idx = max(entry.get("update_idx", 0) for entry in new_updates)

    return ws
