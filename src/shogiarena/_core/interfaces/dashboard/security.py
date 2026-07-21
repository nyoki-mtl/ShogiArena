"""ダッシュボード HTTP サーバーの local single-user セキュリティ境界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web

from shogiarena._core.shared.kernel.network_hosts import is_loopback_host, normalize_host_name

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DASHBOARD_READ_ONLY_KEY = web.AppKey("shogiarena.dashboard.read_only", bool)


def require_loopback_bind_host(host: str) -> str:
    """ローカル限定ダッシュボードの bind host を検証・正規化する。"""

    normalized = host.strip()
    if not is_loopback_host(normalized):
        raise ValueError(
            "Dashboard bind host must be an explicit loopback address (localhost, 127.0.0.0/8, or ::1); "
            "non-loopback dashboard access requires an authentication design"
        )
    return normalized


def _split_http_host(value: str, *, scheme: str) -> tuple[str, int] | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0:
            return None
        host = raw[1:closing]
        remainder = raw[closing + 1 :]
        if not remainder:
            port = 443 if scheme == "https" else 80
        elif remainder.startswith(":") and remainder[1:].isdigit():
            port = int(remainder[1:])
        else:
            return None
    else:
        if raw.count(":") > 1:
            return None
        host_part, separator, port_part = raw.rpartition(":")
        if separator:
            if not port_part.isdigit():
                return None
            host = host_part
            port = int(port_part)
        else:
            host = raw
            port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        return None
    return normalize_host_name(host), port


def _is_same_origin(request: web.Request, origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"}:
        return False
    if parsed.query or parsed.fragment:
        return False
    request_authority = _split_http_host(request.host, scheme=request.scheme)
    if request_authority is None:
        return False
    request_host, request_port = request_authority
    normalized_origin_host = normalize_host_name(parsed.hostname)
    effective_origin_port = origin_port or (443 if parsed.scheme == "https" else 80)
    return (
        parsed.scheme == request.scheme
        and normalized_origin_host == request_host
        and effective_origin_port == request_port
    )


def _security_error(
    message: str,
    *,
    status: int,
    code: str = "dashboard_security_rejected",
) -> web.Response:
    return web.json_response(
        {"detail": message, "error": message, "code": code},
        status=status,
    )


@web.middleware
async def local_dashboard_security_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """v1 の Host、Origin、mutation content-type ポリシーを適用する。"""

    request_authority = _split_http_host(request.host, scheme=request.scheme)
    if request_authority is None or not is_loopback_host(request_authority[0]):
        return _security_error("Dashboard requests require a loopback Host header", status=403)

    origin = request.headers.get("Origin")
    if origin is not None and not _is_same_origin(request, origin):
        return _security_error("Dashboard request Origin must match the loopback dashboard origin", status=403)

    if request.method in _UNSAFE_METHODS:
        if request.app.get(DASHBOARD_READ_ONLY_KEY, False):
            return _security_error(
                "Archived dashboard mode is read-only",
                status=403,
                code="dashboard_read_only",
            )
        if origin is None:
            return _security_error("Dashboard mutation requests require an Origin header", status=403)
        if request.content_type != "application/json":
            return _security_error("Dashboard mutation requests require Content-Type: application/json", status=415)

    return await handler(request)


__all__ = [
    "DASHBOARD_READ_ONLY_KEY",
    "local_dashboard_security_middleware",
    "require_loopback_bind_host",
]
