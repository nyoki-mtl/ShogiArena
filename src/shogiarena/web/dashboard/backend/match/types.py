"""マッチダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import Literal, TypedDict

from shogiarena.web.dashboard.backend.live.types import LiveViewSnapshot

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


# ---------------------------------------------------------------------------
# Match payload types
# ---------------------------------------------------------------------------


class ColorCounts(TypedDict):
    """先手・後手別の WDL カウント。"""

    black: WdlGamesCount
    white: WdlGamesCount


class MatchGames(TypedDict):
    """マッチ対局の進捗カウント。"""

    completed: int
    total: int | None
    wins: int
    losses: int
    draws: int


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


class MatchPayload(TypedDict, total=False):
    """``_build_match_payload`` が返すマッチサマリレスポンス。"""

    mode: Literal["match"]
    summarySource: str
    tested: str
    baseline: str
    games: MatchGames
    winRate: float | None
    winRateCi95: ConfidenceInterval
    eloEstimate: float | None
    eloCi95: ConfidenceInterval
    colors: ColorCounts
    timeline: list[MatchTimelineEntry]
    timestamp: str
    liveView: LiveViewSnapshot
