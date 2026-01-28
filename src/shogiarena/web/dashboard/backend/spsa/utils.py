"""SPSA utility functions for type coercion, variant handling, and calculations."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from shogiarena.arena.orchestrators.spsa.identifiers import variant_token as core_variant_token
from shogiarena.arena.tuning.param_io import compute_variant_id_from_mapping


def coerce_int(value: Any) -> int | None:
    """Coerce value to int, returning None if not possible."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return int(value)
        return None
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            return int(token)
        except ValueError:
            return None
    return None


def coerce_float(value: Any) -> float | None:
    """Coerce value to float, returning None if not possible."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def coerce_timestamp_ms(value: Any) -> int | None:
    """Coerce value to timestamp in milliseconds."""
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            try:
                normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
                dt = datetime.fromisoformat(normalized)
                return int(dt.timestamp() * 1000)
            except ValueError:
                return None
        else:
            return int(numeric)
    return None


def timestamp_to_iso(value: Any) -> str | None:
    """Convert timestamp (ms) to ISO format string."""
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def variant_token(update_idx: Any) -> str:
    """Convert update index to variant token string (e.g., 'v000001')."""
    idx = coerce_int(update_idx)
    if idx is None:
        return core_variant_token(-1)
    return core_variant_token(idx)


def resolve_variant_id(update_idx: Any, *, allow_base: bool = True) -> str:
    """Resolve update index to variant ID token."""
    idx_value = coerce_int(update_idx)
    if idx_value is None or idx_value < 0:
        return core_variant_token(-1) if allow_base else core_variant_token(-1)

    return variant_token(idx_value)


def format_variant_label(update_idx: Any, *, allow_base: bool = True) -> str:
    """Format variant label from update index."""
    idx_value = coerce_int(update_idx)
    resolved = resolve_variant_id(idx_value, allow_base=allow_base)
    return resolved or "v000000"


def extract_variant_from_game_id(game_id: str | None) -> str | None:
    """Extract variant token from game ID string."""
    if not game_id:
        return None
    token = str(game_id)
    if token.startswith("v") and "-" in token:
        return token.split("-", 1)[0]
    return None


def compute_variant_id_safe(params: dict[str, float], types_map: dict[str, str] | None = None) -> str | None:
    """Compute variant ID from parameters, safely handling missing types."""
    return compute_variant_id_from_mapping(params, types=types_map or {}, sort_by_name=True)
