"""トーナメントダッシュボードで使用される TypedDict 定義。

各 payload builder が返す dict を型付けし、API 契約を明示化する。
``total=False`` で定義されたフィールドはオプションであり、
データソースによって存在しない場合がある。
"""

from __future__ import annotations

from typing import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject

# ---------------------------------------------------------------------------
# Standings types
# ---------------------------------------------------------------------------


class StandingEntry(TypedDict):
    """順位表の各エンジンエントリ。"""

    engine: str
    points: float
    games: float
    wins: float
    draws: float
    losses: float
    win_rate: float
    rating: float
    rank: int


class StandingsPayload(TypedDict):
    """``build_standings_payload`` が返す順位表レスポンス。"""

    standings: list[StandingEntry]
    engines_meta: list[JsonObject]
    updated_at: str


class GamesCounter(TypedDict):
    """進捗表示用の対局カウンタ。"""

    completed: int
    total: int
    cancelled: int


class ProgressPayload(TypedDict):
    """``build_progress_payload`` が返す進捗レスポンス。"""

    games: GamesCounter
    in_progress: int
    pending: int
    completion_rate: float | int
    estimated_time_remaining: str
    updated_at: str


# ---------------------------------------------------------------------------
# Stats payload types
# ---------------------------------------------------------------------------


class HeadToHeadPayload(TypedDict):
    """``build_head_to_head_payload`` が返す head-to-head レスポンス。"""

    head_to_head: list[JsonObject]
    updated_at: str


# ---------------------------------------------------------------------------
# Pair stats types
# ---------------------------------------------------------------------------


class PairStatsEntry(TypedDict):
    """ペア統計エントリ。``build_pair_stats_entries`` が返す。"""

    pair_id: str
    engines: list[str]
    games: int
    wins: dict[str, int]
    draws: int
    win_rate: dict[str, float | None]
    los: dict[str, float | None]


class PairStatsPayload(TypedDict):
    """``build_pair_stats_payload`` が返すペア統計レスポンス。"""

    pairs: list[PairStatsEntry]
    total_pairs: int
    signature: str
    source: str
    fetched_at: str
