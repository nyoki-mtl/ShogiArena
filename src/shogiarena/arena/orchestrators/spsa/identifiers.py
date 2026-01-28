from __future__ import annotations

import base64
import hashlib
import time
from typing import Literal

PhaseLiteral = Literal["plus", "minus", "ltc"]


def variant_token(update_idx: int) -> str:
    if update_idx <= 0:
        return "v000000"
    normalized = int(update_idx)
    return f"v{normalized:06d}"


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
