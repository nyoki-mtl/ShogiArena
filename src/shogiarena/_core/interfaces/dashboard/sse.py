"""Shared Server-Sent Events helpers for dashboard APIs."""

from __future__ import annotations

import json

from aiohttp import web

from shogiarena._core.shared.kernel.json_types import JsonObject


def extract_last_event_id(request: web.Request) -> int | None:
    """Return a non-negative Last-Event-ID header value when provided."""
    raw = request.headers.get("Last-Event-ID")
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def inject_resume_from(payload: JsonObject, last_event_id: int | None) -> JsonObject:
    """Add resume_from to payload only when it is not already present."""
    if last_event_id is None:
        return payload
    if "resume_from" in payload:
        return payload
    next_payload = dict(payload)
    next_payload["resume_from"] = last_event_id
    return next_payload


def serialize_sse_event(event_type: str, payload: JsonObject, *, event_id: str | None = None) -> bytes:
    """Serialize an SSE envelope."""
    parts = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_type}")
    parts.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return ("\n".join(parts) + "\n\n").encode()


__all__ = [
    "extract_last_event_id",
    "serialize_sse_event",
    "inject_resume_from",
]
