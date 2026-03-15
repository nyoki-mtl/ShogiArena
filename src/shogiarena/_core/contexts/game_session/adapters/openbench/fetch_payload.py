"""Remote OpenBench config fetch helpers for create payload building."""

from __future__ import annotations

import json

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text, is_strict_numeric


def fetch_engine_nps(
    engine_name: str,
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> int:
    import urllib.parse
    import urllib.request

    endpoint = f"{openbench_server.rstrip('/')}/api/config/{urllib.parse.quote(engine_name)}/"
    data = urllib.parse.urlencode({"username": openbench_username or "", "password": openbench_password}).encode(
        "utf-8"
    )
    request = urllib.request.Request(endpoint, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to fetch OpenBench engine config for scale_nps=auto: {engine_name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"OpenBench api/config returned invalid payload for engine: {engine_name}")
    if "error" in payload:
        raise ValueError(f"OpenBench api/config error for engine '{engine_name}': {payload['error']}")
    nps_raw = payload.get("nps")
    if not is_strict_numeric(nps_raw):
        raise ValueError(f"OpenBench api/config missing nps for engine '{engine_name}'")
    nps = coerce_int(nps_raw)
    if nps is None:
        raise ValueError(f"OpenBench api/config has invalid nps for engine '{engine_name}'")
    if nps <= 0:
        raise ValueError(f"OpenBench api/config has non-positive nps for engine '{engine_name}'")
    return nps


def fetch_books(
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> list[str]:
    import urllib.parse
    import urllib.request

    endpoint = f"{openbench_server.rstrip('/')}/api/config/"
    data = urllib.parse.urlencode({"username": openbench_username or "", "password": openbench_password}).encode(
        "utf-8"
    )
    request = urllib.request.Request(endpoint, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Failed to fetch OpenBench api/config payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("OpenBench api/config returned invalid payload")
    if "error" in payload:
        raise ValueError(f"OpenBench api/config error: {payload['error']}")
    books_raw = payload.get("books")
    if isinstance(books_raw, dict):
        books = []
        for key in books_raw.keys():
            normalized = coerce_optional_text(key)
            if normalized:
                books.append(normalized)
    elif isinstance(books_raw, list):
        books = []
        for item in books_raw:
            normalized = coerce_optional_text(item)
            if normalized:
                books.append(normalized)
    else:
        return []
    return sorted(set(books))


__all__ = ["fetch_books", "fetch_engine_nps"]
