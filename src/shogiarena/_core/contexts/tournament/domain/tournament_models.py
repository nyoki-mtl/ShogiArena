"""Tournament schedule/result models shared by orchestration layers."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass

from shogiarena._core.shared.kernel.initial_position_entry import InitialPositionEntry
from shogiarena._core.shared.kernel.json_types import JsonObject

_GAME_SPEC_ID_VERSION = "v1"
"""Internal ID version marker. Not exposed as a public parameter."""


@dataclass(kw_only=True)
class GameSpec:
    black_engine: str
    white_engine: str
    initial_sfen: str
    game_id: str
    round_num: int = 0
    display_order: int | None = None
    pair_key: str | None = None
    pair_slot: int | None = None
    pair_index: int | None = None
    matchup_key: str | None = None
    opening_line_id: str | None = None
    opening_line_moves_usi: tuple[str, ...] = ()
    opening_source: str | None = None
    opening_source_line_no: int | None = None
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

    def to_schedule_metadata(self, *, display_order: int | None = None) -> JsonObject:
        """Return JSON-safe schedule metadata for record attributes."""

        order = display_order if display_order is not None else self.display_order
        payload: JsonObject = {
            "schema_version": 1,
            "round_num": self.round_num,
            "initial_sfen": self.initial_sfen,
        }
        if order is not None:
            payload["display_order"] = order
        if self.pair_key is not None:
            payload["pair_key"] = self.pair_key
        if self.pair_slot is not None:
            payload["pair_slot"] = self.pair_slot
        if self.pair_index is not None:
            payload["pair_index"] = self.pair_index
        if self.matchup_key is not None:
            payload["matchup_key"] = self.matchup_key
        if self.opening_line_id is not None:
            payload["opening_line_id"] = self.opening_line_id
        if self.opening_source is not None:
            payload["opening_source"] = self.opening_source
        if self.opening_source_line_no is not None:
            payload["opening_source_line_no"] = self.opening_source_line_no
        if self.opening_line_moves_usi:
            payload["opening_line_moves_usi"] = list(self.opening_line_moves_usi)
        return payload


__all__ = ["GameSpec", "InitialPositionEntry"]
