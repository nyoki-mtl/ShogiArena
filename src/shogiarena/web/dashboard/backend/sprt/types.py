"""SPRT ダッシュボードバックエンドで使用される TypedDict 定義。"""

from __future__ import annotations

from typing import Literal, TypedDict

from shogiarena.web.dashboard.backend.live.types import LiveViewSnapshot

# ---------------------------------------------------------------------------
# Config / Status types
# ---------------------------------------------------------------------------


class SprtConfig(TypedDict):
    """SPRT テスト設定。"""

    elo0: float
    elo1: float
    alpha: float
    beta: float
    minGames: int
    maxGames: int | None


class SprtStatus(TypedDict):
    """SPRT テスト現在ステータス。"""

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


class SprtGames(TypedDict):
    """SPRT 対局進捗。"""

    completed: int
    total: int | None


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


class SprtPayload(TypedDict, total=False):
    """``_build_sprt_payload`` が返す SPRT サマリレスポンス。"""

    mode: Literal["sprt"]
    summarySource: str
    tested: str
    baseline: str
    config: SprtConfig
    status: SprtStatus
    games: SprtGames
    timeline: list[SprtTimelineEntry]
    timestamp: str
    liveView: LiveViewSnapshot
