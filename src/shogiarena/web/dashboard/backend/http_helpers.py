"""Utility helpers for aiohttp JSON responses."""

from __future__ import annotations

from typing import Any

from aiohttp import web


def json_error_response(
    detail: str,
    *,
    status: int = 400,
    code: str | None = None,
    **extra: Any,
) -> web.Response:
    """Return a JSON error response with a consistent payload structure.

    Args:
        detail: Human readable error message.
        status: HTTP status code to return.
        code: Optional machine readable error code.
        extra: Additional key/value pairs to include in the payload.
    """
    payload: dict[str, Any] = {"detail": detail, "error": detail}
    if code is not None:
        payload["code"] = code
    if extra:
        payload.update(extra)
    return web.json_response(payload, status=status)


__all__ = ["json_error_response"]
