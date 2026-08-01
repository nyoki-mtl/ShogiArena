"""Higher-level runtime helpers for dashboard SSE endpoints."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from aiohttp import web

from shogiarena._core.shared.kernel.json_types import JsonObject

from .sse import extract_last_event_id, inject_resume_from, serialize_sse_event

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}
_SSE_DISCONNECT_ERRORS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

WrapSsePayload = Callable[[str, JsonObject], tuple[JsonObject, int]]


class PrepareSseResponse(Protocol):
    def __call__(
        self,
        response: web.StreamResponse,
        request: web.Request,
        *,
        log_context: str,
    ) -> Awaitable[bool]: ...


class _SseStreamRuntime:
    """Prepared SSE response wrapper with envelope and heartbeat helpers."""

    def __init__(
        self,
        *,
        response: web.StreamResponse,
        stream_key: str,
        event_name: str,
        wrap_payload: WrapSsePayload,
        last_event_id: int | None,
    ) -> None:
        self.response = response
        self._stream_key = stream_key
        self._event_name = event_name
        self._wrap_payload = wrap_payload
        self._last_event_id = last_event_id

    async def push_event(self, payload: JsonObject) -> None:
        payload = inject_resume_from(payload, self._last_event_id)
        wrapped, seq = self._wrap_payload(self._stream_key, payload)
        chunk = serialize_sse_event(self._event_name, wrapped, event_id=str(seq))
        await self.response.write(chunk)

    async def push_heartbeat(self) -> None:
        await self.push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})

    async def push_comment(self, comment: str = "heartbeat") -> None:
        """Write an SSE comment without changing the client's Last-Event-ID."""

        await self.response.write(f": {comment}\n\n".encode())

    async def finalize(self, *, logger: logging.Logger, log_message: str) -> None:
        try:
            await self.response.write_eof()
        except _SSE_DISCONNECT_ERRORS as exc:
            logger.debug("%s: %s", log_message, exc)


async def prepare_sse_runtime(
    request: web.Request,
    *,
    log_context: str,
    stream_key: str,
    event_name: str,
    prepare_response: PrepareSseResponse,
    wrap_payload: WrapSsePayload,
) -> tuple[web.StreamResponse, _SseStreamRuntime | None]:
    """Create and prepare an SSE response plus runtime helper."""

    response = web.StreamResponse(status=200, reason="OK", headers=dict(_SSE_HEADERS))
    if not await prepare_response(response, request, log_context=log_context):
        return response, None
    runtime = _SseStreamRuntime(
        response=response,
        stream_key=stream_key,
        event_name=event_name,
        wrap_payload=wrap_payload,
        last_event_id=extract_last_event_id(request),
    )
    return response, runtime


__all__ = ["prepare_sse_runtime"]
