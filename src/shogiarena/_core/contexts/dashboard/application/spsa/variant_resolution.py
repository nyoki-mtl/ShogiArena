"""SPSA variant resolution and token formatting."""

from __future__ import annotations

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int
from shogiarena._core.shared.kernel.variant_tokens import format_variant_token

__all__ = [
    "extract_variant_from_game_id",
    "format_variant_label",
    "resolve_variant_id",
    "variant_token",
]


_VariantInput = JsonValue | None


def variant_token(update_idx: _VariantInput) -> str:
    """Convert update index to variant token string (e.g., 'v000001')."""
    idx = coerce_int(update_idx)
    if idx is None:
        return format_variant_token(-1)
    return format_variant_token(idx)


def resolve_variant_id(update_idx: _VariantInput) -> str:
    """Resolve update index to variant ID token."""
    idx_value = coerce_int(update_idx)
    if idx_value is None or idx_value < 0:
        return format_variant_token(-1)

    return variant_token(idx_value)


def format_variant_label(update_idx: _VariantInput) -> str:
    """Format variant label from update index."""
    idx_value = coerce_int(update_idx)
    return resolve_variant_id(idx_value)


def extract_variant_from_game_id(game_id: str | None) -> str | None:
    """Extract variant token from game ID string."""
    if not game_id:
        return None
    token = str(game_id)
    if token.startswith("v") and "-" in token:
        return token.split("-", 1)[0]
    return None
