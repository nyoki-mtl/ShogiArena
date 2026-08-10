"""Stable dashboard identity for a CSA game."""

from __future__ import annotations

import hashlib


def csa_game_key(run_id: str, server_game_id: str) -> str:
    """Return an opaque, route-safe key for one game within one bridge run."""
    payload = f"{run_id}\0{server_game_id}".encode()
    return f"csa_{hashlib.sha256(payload).hexdigest()}"


__all__ = ["csa_game_key"]
