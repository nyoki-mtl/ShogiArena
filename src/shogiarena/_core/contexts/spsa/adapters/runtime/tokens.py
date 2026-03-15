"""Token helpers for SPSA runtime identifiers."""

from __future__ import annotations

import base64
import hashlib
import time

from shogiarena._core.contexts.spsa.domain.spsa_models import PhaseLiteral
from shogiarena._core.shared.kernel.variant_tokens import format_variant_token


def variant_token(update_idx: int) -> str:
    return format_variant_token(update_idx)


def phase_symbol(phase: PhaseLiteral) -> str:
    if phase == "plus":
        return "+"
    if phase == "minus":
        return "-"
    return ""


def _short_digest(*components: str, length: int = 8) -> str:
    payload = "|".join(components)
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=6).digest()
    token = base64.b32encode(digest).decode("ascii").rstrip("=")
    return token.lower()[:length]


def make_game_token(seq: int) -> str:
    return _short_digest(str(seq), str(time.time_ns()))


__all__ = [
    "PhaseLiteral",
    "make_game_token",
    "phase_symbol",
    "variant_token",
]
