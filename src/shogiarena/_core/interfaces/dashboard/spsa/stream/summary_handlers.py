"""Summary/LTC result stream endpoint handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.ports.spsa_payloads import UpdateEntry
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse_runtime import prepare_sse_runtime
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

from .query_models import LtcResultsStreamQuery, SpsaSummaryStreamQuery, UpdatesStreamQuery

logger = logging.getLogger(__name__)

# Retain references to fire-and-forget notify tasks so they are not garbage collected
# before completion (CPython only holds a weak reference to running tasks otherwise).
_PENDING_NOTIFY_TASKS: set[asyncio.Task[None]] = set()


def publish_summary_snapshot(handler, payload: Mapping[str, JsonValue]) -> None:
    snapshot = to_json_object(payload)
    handler._latest_summary = snapshot

    async def _notify() -> None:
        async with handler._summary_condition:
            handler._summary_condition.notify_all()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop yet; snapshot will be served on next poll/connection
        return
    task = loop.create_task(_notify())
    _PENDING_NOTIFY_TASKS.add(task)
    task.add_done_callback(_PENDING_NOTIFY_TASKS.discard)


async def sse_summary(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming SPSA summary snapshots."""
    if handler._summary_supplier is None and handler._latest_summary is None:
        return json_error_response("spsa summary unavailable", status=503, code="summary_unavailable")
    try:
        parsed_query = SpsaSummaryStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_summary",
        stream_key="summary",
        event_name="spsa_summary",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    update_timeout = parsed_query.poll_interval
    update_timeout = max(0.5, min(update_timeout, 60.0))
    should_send_initial = parsed_query.should_send_initial
    last_signature: str | None = None

    def resolve_snapshot() -> JsonObject | None:
        if handler._latest_summary is not None:
            return dict(handler._latest_summary)
        if handler._summary_supplier is None:
            return None
        snapshot = handler._summary_supplier()
        if snapshot is not None:
            handler._latest_summary = dict(snapshot)
        return snapshot

    try:
        if should_send_initial:
            initial = resolve_snapshot()
            if initial:
                last_signature = json.dumps(initial, sort_keys=True)
                await runtime.push_event(
                    {
                        "type": "summary",
                        "data": initial,
                        "timestamp": int(time.time() * 1000),
                    }
                )
            should_send_initial = False

        while True:
            snapshot: JsonObject | None = None
            try:
                async with handler._summary_condition:
                    await asyncio.wait_for(handler._summary_condition.wait(), timeout=update_timeout)
                if handler._latest_summary is not None:
                    snapshot = dict(handler._latest_summary)
            except TimeoutError:
                snapshot = None

            if snapshot:
                signature = json.dumps(snapshot, sort_keys=True)
                if signature != last_signature:
                    await runtime.push_event(
                        {
                            "type": "summary",
                            "data": snapshot,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                continue

            await runtime.push_heartbeat()
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA summary SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA summary SSE response")

    return response


async def sse_ltc_results(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming LTC regression results."""
    if handler._store is None or handler._ltc_service is None or handler._snapshot_service is None:
        return json_error_response("ltc regression unavailable", status=503, code="ltc_unavailable")
    try:
        parsed_query = LtcResultsStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_ltc_results",
        stream_key="ltc_results",
        event_name="spsa_ltc_results",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_sse_payload,
    )
    if runtime is None:
        return response

    limit = parsed_query.limit
    limit = max(1, min(limit, 500))

    poll_interval = parsed_query.poll_interval
    poll_interval = max(1.0, min(poll_interval, 30.0))
    should_send_initial = parsed_query.should_send_initial
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()
    last_signature: str | None = None

    try:
        while True:
            data_payload = handler._snapshot_service.compose_ltc_results_snapshot(limit=limit)
            signature = json.dumps(data_payload, sort_keys=True)

            if should_send_initial or signature != last_signature:
                await runtime.push_event(
                    {
                        "type": "ltc_results",
                        "data": data_payload,
                        "timestamp": int(time.time() * 1000),
                    }
                )
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
        logger.debug("SPSA LTC results SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA LTC results SSE response")

    return response


async def sse_ltc_progress(handler, request: web.Request) -> web.StreamResponse:
    """SSE endpoint for streaming LTC progress updates."""
    try:
        parsed_query = UpdatesStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_ltc_progress",
        stream_key="ltc_progress",
        event_name="spsa_ltc_progress",
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
    last_signatures: dict[int, str] = {}

    def ltc_signature(entry: UpdateEntry) -> str:
        payload = {
            "ltc_regression": entry.get("ltc_regression"),
            "has_ltc_regression": entry.get("has_ltc_regression"),
            "is_ltc_rejected": entry.get("is_ltc_rejected"),
            "ltc_reverted_to": entry.get("ltc_reverted_to"),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    def extract_ltc_update(entry: UpdateEntry) -> JsonObject | None:
        idx_value = entry.get("update_idx")
        if not isinstance(idx_value, int):
            return None
        ltc_update: JsonObject = {
            "update_idx": idx_value,
            "ltc_regression": json_serialize(entry.get("ltc_regression")),
            "has_ltc_regression": entry.get("has_ltc_regression"),
            "is_ltc_rejected": entry.get("is_ltc_rejected"),
            "ltc_reverted_to": entry.get("ltc_reverted_to"),
        }
        return ltc_update

    try:
        if should_send_initial:
            updates = handler._update_query_service.load_index_updates()
            initial_updates: list[JsonObject] = []
            for entry in updates:
                idx_value = entry.get("update_idx")
                if isinstance(idx_value, int):
                    last_signatures[idx_value] = ltc_signature(entry)
                    if entry.get("has_ltc_regression") or entry.get("ltc_regression") or entry.get("is_ltc_rejected"):
                        ltc_entry = extract_ltc_update(entry)
                        if ltc_entry:
                            initial_updates.append(ltc_entry)
            if initial_updates:
                await runtime.push_event(
                    {
                        "type": "ltc_progress",
                        "updates": initial_updates,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                should_send_initial = False

        while True:
            updates = handler._update_query_service.load_index_updates()
            if updates:
                changed: list[JsonObject] = []
                for entry in updates:
                    idx_value = entry.get("update_idx")
                    if not isinstance(idx_value, int):
                        continue
                    signature = ltc_signature(entry)
                    if signature != last_signatures.get(idx_value):
                        last_signatures[idx_value] = signature
                        if (
                            entry.get("has_ltc_regression")
                            or entry.get("ltc_regression")
                            or entry.get("is_ltc_rejected")
                        ):
                            ltc_entry = extract_ltc_update(entry)
                            if ltc_entry:
                                changed.append(ltc_entry)
                if changed:
                    await runtime.push_event(
                        {
                            "type": "ltc_progress",
                            "updates": changed,
                            "timestamp": int(time.time() * 1000),
                        }
                    )

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                await runtime.push_heartbeat()
                last_heartbeat = now

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
        logger.debug("SPSA LTC progress SSE client disconnected: %s", exc)
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA LTC progress SSE response")

    return response
