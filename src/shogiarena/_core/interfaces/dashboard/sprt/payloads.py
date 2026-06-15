"""SPRT ダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Timeline / Payload types
# ---------------------------------------------------------------------------


class SprtTimelineEntry(TypedDict):
    """SPRT タイムラインの各エントリ。"""

    game_index: int
    llr: float
    lower: float
    upper: float
    decision: str
    wins: int
    draws: int
    losses: int
    games: int
    win_rate: float | None
    elo_estimate: float | None
    pending_pairs: int
    pending_games: int


__all__ = ["SprtTimelineEntry"]
