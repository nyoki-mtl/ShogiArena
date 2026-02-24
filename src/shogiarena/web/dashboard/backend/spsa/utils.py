"""SPSA utility functions for variant handling and calculations.

型変換関数は :mod:`shogiarena.utils.types.coerce` に統合済み。
本モジュールでは下位互換のため再 export する。
"""

from __future__ import annotations

from typing import Any

from shogiarena.arena.orchestrators.spsa.identifiers import variant_token as core_variant_token
from shogiarena.arena.tuning.param_io import compute_variant_id_from_mapping
from shogiarena.utils.types.coerce import (
    coerce_float,
    coerce_int,
    coerce_timestamp_ms,
    timestamp_to_iso,
)

# Re-export for existing consumers
__all__ = [
    "coerce_float",
    "coerce_int",
    "coerce_timestamp_ms",
    "compute_variant_id_safe",
    "extract_variant_from_game_id",
    "format_variant_label",
    "resolve_variant_id",
    "timestamp_to_iso",
    "variant_token",
]


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
