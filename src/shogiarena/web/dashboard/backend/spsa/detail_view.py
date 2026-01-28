"""Helpers for tailoring SPSA detail payloads to view/include parameters."""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import StatisticsError, fmean, pstdev
from typing import Any

from aiohttp import web

DetailDict = dict[str, Any]

SUPPORTED_VIEWS: tuple[str, ...] = ("slim", "full")
SUPPORTED_WINDOWS: tuple[str, ...] = ("short", "long")
WINDOW_SAMPLE_LIMITS: dict[str, int] = {
    "short": 32,
    "long": 128,
}
SUPPORTED_INCLUDES: tuple[str, ...] = (
    "variant_games",
    "ltc_games",
    "score_history",
    "raw_payload",
)


@dataclass(frozen=True, slots=True)
class DetailViewConfig:
    """Parsed detail view configuration extracted from query parameters."""

    view: str
    includes: frozenset[str]
    window: str


def parse_detail_view_config(request: web.Request) -> DetailViewConfig:
    """Parse view/include query parameters for SPSA detail endpoints."""

    query = request.rel_url.query
    raw_view = (query.get("view") or "slim").strip().lower()
    if raw_view not in SUPPORTED_VIEWS:
        raise ValueError("unsupported detail view")

    raw_window = (query.get("window") or "short").strip().lower()
    if raw_window not in SUPPORTED_WINDOWS:
        raise ValueError("unsupported detail window")

    include_tokens: list[str] = []
    if hasattr(query, "getall"):
        include_tokens.extend(query.getall("include", []))
    else:
        raw = query.get("include")
        if raw is not None:
            include_tokens.append(raw)

    normalized: set[str] = set()
    for token in include_tokens:
        if token is None:
            continue
        for part in str(token).split(","):
            candidate = part.strip().lower()
            if not candidate:
                continue
            if candidate == "all":
                normalized.update(SUPPORTED_INCLUDES)
                continue
            if candidate in SUPPORTED_INCLUDES:
                normalized.add(candidate)

    includes = frozenset(normalized)
    if raw_view == "full":
        return DetailViewConfig(view="full", includes=frozenset(SUPPORTED_INCLUDES), window="full")

    return DetailViewConfig(view="slim", includes=includes, window=raw_window)


def apply_detail_view(detail: DetailDict, config: DetailViewConfig) -> DetailDict:
    """Return a copy of detail data filtered according to the view config."""

    response = copy.deepcopy(detail)
    view = config.view
    requested_includes = set(config.includes)
    loaded_includes: set[str] = set()
    window_label = config.window if view == "slim" else "full"

    available_includes = _resolve_available_includes(detail)

    if view == "slim":
        _prune_variant_games(response, requested_includes, loaded_includes)
        _prune_ltc_games(response, requested_includes, loaded_includes)
        _prune_payload(response, requested_includes, loaded_includes, window_label)
    else:
        loaded_includes.update(include for include in SUPPORTED_INCLUDES if include in available_includes)

    response["meta"] = {
        "view": view,
        "window": window_label,
        "requested_includes": sorted(requested_includes),
        "loaded_includes": sorted(loaded_includes),
        "available_includes": sorted(available_includes),
    }
    return response


def _resolve_available_includes(detail: Mapping[str, Any]) -> set[str]:
    available: set[str] = set()

    games_count = detail.get("games_count")
    games = detail.get("games")
    if _has_content(games_count) or (isinstance(games, Sequence) and len(games) > 0):
        available.add("variant_games")

    ltc_games_count = detail.get("ltc_games_count")
    ltc_games = detail.get("ltc_games")
    if _has_content(ltc_games_count) or (isinstance(ltc_games, Sequence) and len(ltc_games) > 0):
        available.add("ltc_games")

    payload = detail.get("payload")
    if isinstance(payload, Mapping) and payload:
        available.add("raw_payload")
        if "score_history" in payload or "scoreHistory" in payload:
            available.add("score_history")

    return available


def _has_content(value: Any) -> bool:
    if isinstance(value, int | float):
        return bool(value)
    return False


def _prune_variant_games(response: DetailDict, requested: set[str], loaded: set[str]) -> None:
    if "variant_games" in requested:
        loaded.add("variant_games")
        return
    response["games"] = []


def _prune_ltc_games(response: DetailDict, requested: set[str], loaded: set[str]) -> None:
    if "ltc_games" in requested:
        loaded.add("ltc_games")
        return
    response["ltc_games"] = []


PAYLOAD_HEAVY_KEYS = {
    "games",
    "ltc_games",
    "games_meta",
    "ltc_games_meta",
    "raw_events",
    "score_history",
    "scoreHistory",
}


def _prune_payload(response: DetailDict, requested: set[str], loaded: set[str], window: str) -> None:
    include_raw_payload = "raw_payload" in requested
    include_score_history = "score_history" in requested
    if include_raw_payload:
        loaded.add("raw_payload")
    payload_obj = response.get("payload")
    if not isinstance(payload_obj, dict):
        response["payload"] = {"view": "slim"}
        return
    if include_raw_payload:
        if include_score_history and (_contains_score_history(payload_obj)):
            loaded.add("score_history")
        return
    slim_payload: dict[str, Any] = {}
    digest: dict[str, Any] | None = None
    if not include_score_history:
        digest = _build_score_history_digest(payload_obj, window)

    for key, value in payload_obj.items():
        lowered = key.lower()
        if lowered in {"score_history"}:
            if include_score_history:
                slim_payload["score_history"] = value
            elif digest is not None:
                slim_payload["score_history"] = digest
            continue
        if key in PAYLOAD_HEAVY_KEYS:
            continue
        slim_payload[key] = value
    slim_payload["view"] = "slim"
    if include_score_history and _contains_score_history(payload_obj):
        loaded.add("score_history")
    response["payload"] = slim_payload


def _contains_score_history(payload: Mapping[str, Any]) -> bool:
    return "score_history" in payload or "scoreHistory" in payload


def _build_score_history_digest(payload: Mapping[str, Any], window: str) -> dict[str, Any]:
    history_obj = payload.get("score_history") or payload.get("scoreHistory")
    if isinstance(history_obj, Mapping) and history_obj.get("view") == "digest" and history_obj.get("window") == window:
        return dict(history_obj)

    samples = _extract_score_samples(history_obj)
    limit = WINDOW_SAMPLE_LIMITS.get(window, WINDOW_SAMPLE_LIMITS["short"])
    trimmed = list(samples[-limit:]) if limit and samples else list(samples)

    window_mean: float | None
    window_sigma: float | None
    if trimmed:
        try:
            window_mean = fmean(trimmed)
        except (StatisticsError, ValueError):
            window_mean = None
        try:
            window_sigma = pstdev(trimmed)
        except (StatisticsError, ValueError):
            window_sigma = 0.0
    else:
        window_mean = None
        window_sigma = None

    digest = {
        "view": "digest",
        "window": window,
        "sample_count": len(samples),
        "window_sample_count": len(trimmed),
        "last_sample": trimmed[-1] if trimmed else None,
        "window_mean": window_mean,
        "window_sigma": window_sigma,
        "min": min(trimmed) if trimmed else None,
        "max": max(trimmed) if trimmed else None,
        "samples": trimmed,
    }
    return digest


def _extract_score_samples(source: Any) -> list[float]:
    raw_samples: Sequence[Any] | None = None
    if isinstance(source, Mapping):
        candidate = source.get("samples")
        if isinstance(candidate, Sequence):
            raw_samples = candidate
    elif isinstance(source, Sequence):
        raw_samples = source

    if not raw_samples:
        return []

    samples: deque[float] = deque()
    for entry in raw_samples:
        value: float | None
        if isinstance(entry, int | float):
            value = float(entry)
        elif isinstance(entry, Mapping):
            value_obj = entry.get("value")
            if isinstance(value_obj, int | float):
                value = float(value_obj)
            else:
                value = None
        else:
            value = None
        if value is None or value != value:  # NaN check
            continue
        samples.append(value)
    return list(samples)
