"""SPRT ダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Timeline / Payload types
# ---------------------------------------------------------------------------


class SprtConfig(TypedDict):
    """SPRT 設定。"""

    elo0: float
    elo1: float
    alpha: float
    beta: float
    minGames: int
    maxGames: int


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


__all__ = ["SprtConfig", "SprtTimelineEntry"]
