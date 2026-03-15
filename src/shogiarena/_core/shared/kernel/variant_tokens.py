"""Shared helpers for SPSA variant token formatting."""

from __future__ import annotations


def format_variant_token(update_idx: int) -> str:
    if update_idx <= 0:
        return "v000000"
    return f"v{int(update_idx):06d}"


__all__ = ["format_variant_token"]
