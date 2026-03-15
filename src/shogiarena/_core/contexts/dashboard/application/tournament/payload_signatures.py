"""Local payload hashing/timestamp helpers for dashboard tournament payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from shogiarena._core.shared.kernel.json_coercion import to_json_object


def hash_games_payload(games: Sequence[Mapping[str, object]]) -> str:
    normalized_games = [to_json_object(game) for game in games]
    payload = json.dumps(normalized_games, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def current_timestamp_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


__all__ = ["current_timestamp_iso", "hash_games_payload"]
