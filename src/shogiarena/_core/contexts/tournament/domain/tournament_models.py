"""Tournament schedule/result models shared by orchestration layers."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass

_GAME_SPEC_ID_VERSION = "v1"
"""Internal ID version marker. Not exposed as a public parameter."""


@dataclass
class GameSpec:
    black_engine: str
    white_engine: str
    initial_sfen: str
    game_id: str
    round_num: int = 0
    # Preferred instances for each side when available; None falls back to scheduler defaults
    assigned_instance_black: str | None = None
    assigned_instance_white: str | None = None
    # When True, remote instances should ensure the arena environment is prepared before running
    should_require_install: bool = False

    @classmethod
    def create(cls, black: str, white: str, sfen: str, round_num: int, seed: str) -> GameSpec:
        def normalize(name: str) -> str:
            return re.sub(r"[^a-z0-9]", "", name.lower())

        def round_token(value: int) -> str:
            if value < 0:
                raise ValueError("round_num must be non-negative")
            # Round numbers are now represented as one-based zero-padded decimal strings to
            # keep table ordering intuitive (g0009 -> g0010) and align with user-facing order.
            normalized = value + 1
            return str(normalized).rjust(4, "0")

        def short_digest(*components: str, length: int = 10) -> str:
            payload = "|".join(components)
            digest = hashlib.blake2s(payload.encode(), digest_size=6).digest()
            token = base64.b32encode(digest).decode("ascii").rstrip("=")
            return token.lower()[:length]

        black_norm = normalize(black)
        white_norm = normalize(white)
        hash_components = [black_norm, white_norm, str(round_num), sfen, seed, _GAME_SPEC_ID_VERSION]
        pair_token = short_digest(*hash_components)
        game_id = f"g{round_token(round_num)}-{pair_token}"
        return cls(
            black_engine=black,
            white_engine=white,
            initial_sfen=sfen,
            game_id=game_id,
            round_num=round_num,
        )


__all__ = ["GameSpec"]
