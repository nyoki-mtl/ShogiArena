"""Durable SPSA ledger revision feed."""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web
from pydantic import ValidationError

from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.interfaces.dashboard.sse import extract_last_event_id
from shogiarena._core.interfaces.dashboard.sse_runtime import prepare_sse_runtime

from .query_models import RevisionStreamQuery

logger = logging.getLogger(__name__)

_SNAPSHOT_URL = "/api/spsa/summary"


async def sse_revisions(handler, request: web.Request) -> web.StreamResponse:
    """Notify clients of durable ledger revisions without promising replay."""

    try:
        parsed_query = RevisionStreamQuery.model_validate(dict(request.rel_url.query))
    except ValidationError as exc:
        return json_error_response(str(exc), status=400, code="invalid_query")

    initial_state = await asyncio.to_thread(handler._update_query_service.load_revision_state)
    if initial_state is None:
        return json_error_response(
            "durable SPSA revision feed unavailable",
            status=503,
            code="revision_feed_unavailable",
        )

    response, runtime = await prepare_sse_runtime(
        request,
        log_context="spsa_revisions",
        stream_key="revision",
        event_name="spsa_revision",
        prepare_response=handler._prepare_sse_response,
        wrap_payload=handler._wrap_revision_payload,
    )
    if runtime is None:
        return response

    poll_interval = max(0.2, min(parsed_query.poll_interval, 10.0))
    last_event_id = extract_last_event_id(request)
    last_sent_revision: int | None = None
    previous_revision = last_event_id
    should_send_initial = parsed_query.should_send_initial
    next_state = initial_state
    last_heartbeat = time.monotonic()

    try:
        while True:
            run_id, revision, terminal = next_state
            if should_send_initial or revision != last_sent_revision:
                gap_detected = previous_revision is not None and (
                    revision < previous_revision or revision > previous_revision + 1
                )
                await runtime.push_event(
                    {
                        "type": "revision",
                        "timestamp": int(time.time() * 1000),
                        "data": {
                            "run_id": run_id,
                            "revision": revision,
                            "gap_detected": gap_detected,
                            "snapshot_required": True,
                            "snapshot_url": _SNAPSHOT_URL,
                            "replay_supported": False,
                            "terminal": terminal,
                        },
                    }
                )
                should_send_initial = False
                last_sent_revision = revision
                previous_revision = revision
                last_heartbeat = time.monotonic()
                if terminal:
                    break
            elif time.monotonic() - last_heartbeat >= 15.0:
                await runtime.push_comment()
                last_heartbeat = time.monotonic()

            await asyncio.sleep(poll_interval)
            state = await asyncio.to_thread(handler._update_query_service.load_revision_state)
            if state is None:
                raise RuntimeError("durable SPSA revision feed became unavailable")
            next_state = state
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.debug("SPSA revision SSE client disconnected")
    finally:
        await runtime.finalize(logger=logger, log_message="Failed to finalize SPSA revision SSE response")

    return response


__all__ = ["sse_revisions"]
