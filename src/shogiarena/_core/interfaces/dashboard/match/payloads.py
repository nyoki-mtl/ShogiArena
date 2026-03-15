"""マッチダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class ConfidenceInterval(TypedDict):
    """信頼区間（Elo / 勝率）。"""

    lower: float | None
    upper: float | None


class WdlGamesCount(TypedDict):
    """勝敗引分＋対局数カウント。"""

    wins: int
    losses: int
    draws: int
    games: int


class ColorTimelineEntry(TypedDict):
    """タイムラインエントリ内の先手・後手別統計。"""

    wins: int
    losses: int
    draws: int
    games: int
    winRate: float | None


class MatchTimelineEntry(TypedDict):
    """マッチタイムラインの各エントリ。"""

    gameIndex: int
    wins: int
    losses: int
    draws: int
    games: int
    winRate: float | None
    eloEstimate: float | None
    black: ColorTimelineEntry
    white: ColorTimelineEntry
