from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text, coerce_str_list
from shogiarena._core.shared.kernel.serialization import json_serialize

EngineSyncStrategy = Literal["direct", "wait", "stop"]


def normalize_overlays(raw: JsonValue | tuple[JsonValue, ...] | None) -> list[Path]:
    items = coerce_str_list(raw, field="options_overlays")
    overlays: list[Path] = []
    for item in items:
        candidate = Path(resolve_path_like(item))
        if not candidate.exists():
            raise FileNotFoundError(f"Options overlay file not found: {candidate}")
        overlays.append(candidate)
    return overlays


def load_overlays(overlays: list[Path]) -> JsonObject:
    merged: JsonObject = {}
    for overlay in overlays:
        raw = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise TypeError("options_overlays YAML must be a mapping")
        opts = raw.get("options") if "options" in raw else raw
        if isinstance(opts, Mapping):
            merged.update({str(key): json_serialize(value) for key, value in opts.items()})
    return merged


def normalize_check_templates(raw: JsonValue | tuple[JsonValue, ...] | None) -> tuple[str, ...]:
    return tuple(coerce_str_list(raw, field="isready_lock_check_templates"))


def coerce_optional_positive_int(raw: JsonValue | None, *, field_name: str) -> int | None:
    if raw is None:
        return None
    parsed = coerce_int(raw)
    if parsed is None:
        raise TypeError(f"{field_name} must be an integer or null")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def parse_isready_sync_strategy(raw: JsonValue | None) -> EngineSyncStrategy:
    strategy = coerce_optional_text(raw) or "direct"
    if strategy == "direct":
        return "direct"
    if strategy == "wait":
        return "wait"
    if strategy == "stop":
        return "stop"
    raise ValueError("isready_sync_strategy must be one of: direct, wait, stop")
