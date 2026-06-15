"""Helpers for tailoring SPSA detail payloads to view/include parameters."""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import StatisticsError, fmean, pstdev

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none as _as_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize

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


class _DetailViewQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    view: str = "slim"
    window: str = "short"
    include: list[str] = Field(default_factory=list)

    @field_validator("include", mode="before")
    @classmethod
    def _coerce_include(cls, value: JsonValue | tuple[JsonValue, ...] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                token = coerce_optional_text(item)
                if token is not None:
                    normalized.append(token)
            return normalized
        raise TypeError("include must be a string or list of strings")

    @field_validator("view", mode="before")
    @classmethod
    def _validate_view(cls, value: JsonValue | None) -> str:
        normalized = (coerce_optional_text(value) or "slim").lower()
        if normalized not in SUPPORTED_VIEWS:
            raise ValueError("unsupported detail view")
        return normalized

    @field_validator("window", mode="before")
    @classmethod
    def _validate_window(cls, value: JsonValue | None) -> str:
        normalized = (coerce_optional_text(value) or "short").lower()
        if normalized not in SUPPORTED_WINDOWS:
            raise ValueError("unsupported detail window")
        return normalized


def parse_detail_view_config(request: web.Request) -> DetailViewConfig:
    """Parse view/include query parameters for SPSA detail endpoints."""

    query = request.rel_url.query
    include_tokens_raw: list[str] = query.getall("include", [])
    try:
        parsed = _DetailViewQuery.model_validate(
            {
                "view": query.get("view"),
                "window": query.get("window"),
                "include": include_tokens_raw,
            }
        )
    except ValidationError as exc:
        if "unsupported detail view" in str(exc):
            raise ValueError("unsupported detail view") from exc
        raise ValueError("unsupported detail window") from exc

    normalized: set[str] = set()
    for token in parsed.include:
        for part in token.split(","):
            candidate = part.strip().lower()
            if not candidate:
                continue
            if candidate == "all":
                normalized.update(SUPPORTED_INCLUDES)
                continue
            if candidate in SUPPORTED_INCLUDES:
                normalized.add(candidate)

    includes = frozenset(normalized)
    if parsed.view == "full":
        return DetailViewConfig(view="full", includes=frozenset(SUPPORTED_INCLUDES), window="full")

    return DetailViewConfig(view="slim", includes=includes, window=parsed.window)


def apply_detail_view(detail: JsonObject, config: DetailViewConfig) -> JsonObject:
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


def _resolve_available_includes(detail: Mapping[str, JsonValue]) -> set[str]:
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
    payload_map = _as_json_object(payload) or {}
    if payload_map:
        available.add("raw_payload")
        if "score_history" in payload_map:
            available.add("score_history")

    return available


def _has_content(value: JsonValue | None) -> bool:
    if isinstance(value, int | float):
        return bool(value)
    return False


def _prune_variant_games(response: JsonObject, requested: set[str], loaded: set[str]) -> None:
    if "variant_games" in requested:
        loaded.add("variant_games")
        return
    response["games"] = []


def _prune_ltc_games(response: JsonObject, requested: set[str], loaded: set[str]) -> None:
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
}


def _prune_payload(response: JsonObject, requested: set[str], loaded: set[str], window: str) -> None:
    include_raw_payload = "raw_payload" in requested
    include_score_history = "score_history" in requested
    if include_raw_payload:
        loaded.add("raw_payload")
    payload_obj_raw = response.get("payload")
    payload_obj = _as_json_object(payload_obj_raw)
    if payload_obj is None:
        response["payload"] = {"view": "slim"}
        return
    if include_raw_payload:
        if include_score_history and (_contains_score_history(payload_obj)):
            loaded.add("score_history")
        response["payload"] = payload_obj
        return
    slim_payload: JsonObject = {}
    digest: JsonObject | None = None
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


def _contains_score_history(payload: Mapping[str, JsonValue]) -> bool:
    return "score_history" in payload


def _build_score_history_digest(payload: Mapping[str, JsonValue], window: str) -> JsonObject:
    history_obj = payload.get("score_history")
    history_map = _as_json_object(history_obj)
    if history_map is not None and history_map.get("view") == "digest" and history_map.get("window") == window:
        return dict(history_map)

    samples = _extract_score_samples(history_map if history_map is not None else history_obj)
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


def _extract_score_samples(source: JsonValue | Mapping[str, JsonValue] | Sequence[JsonValue] | None) -> list[float]:
    raw_samples: Sequence[JsonValue] | None = None
    source_map = _as_json_object(source)
    if source_map is not None:
        candidate = source_map.get("samples")
        if isinstance(candidate, list | tuple):
            raw_samples = [json_serialize(entry) for entry in candidate]
    elif isinstance(source, list | tuple):
        raw_samples = [json_serialize(entry) for entry in source]

    if not raw_samples:
        return []

    samples: deque[float] = deque()
    for entry in raw_samples:
        value = coerce_float(entry)
        if value is None:
            entry_map = _as_json_object(entry)
            if entry_map is not None:
                value = coerce_float(entry_map.get("value"))
        if value is None or value != value:  # NaN check
            continue
        samples.append(value)
    return list(samples)
