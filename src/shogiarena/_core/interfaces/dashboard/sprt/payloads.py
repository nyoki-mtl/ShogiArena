"""SPRT ダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Timeline / Payload types
# ---------------------------------------------------------------------------


class SprtTimelineEntry(TypedDict):
    """SPRT タイムラインの各エントリ。"""

    gameIndex: int
    llr: float
    lower: float
    upper: float
    decision: str
    wins: int
    draws: int
    losses: int
    games: int
    winRate: float | None
    eloEstimate: float | None


__all__ = ["SprtTimelineEntry"]
