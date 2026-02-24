"""トーナメントダッシュボードバックエンドで使用される TypedDict 定義。

各エンドポイントが返す dict を型付けし、API 契約を明示化する。
``total=False`` で定義されたフィールドはオプションであり、
データソースによって存在しない場合がある。
"""

from __future__ import annotations

from typing import TypedDict

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
    """``_build_standings_payload`` が返す順位表レスポンス。"""

    standings: list[StandingEntry]
    enginesMeta: list[dict[str, object]]
    updated_at: str


# ---------------------------------------------------------------------------
# Progress types
# ---------------------------------------------------------------------------


class GamesCounter(TypedDict):
    """進捗レスポンス内の対局カウンタ。"""

    completed: int
    total: int
    cancelled: int


class ProgressPayload(TypedDict):
    """``_build_progress_payload`` が返す進捗レスポンス。"""

    games: GamesCounter
    inProgress: int
    pending: int
    completionRate: float | int
    estimatedTimeRemaining: str
    updatedAt: str


# ---------------------------------------------------------------------------
# Game entry types
# ---------------------------------------------------------------------------


class TournamentGameEntry(TypedDict, total=False):
    """対局一覧エントリ。``get_games`` / ``get_match_history`` が返す。"""

    game_id: str | object
    black_player: str | object
    white_player: str | object
    result_code: int | object
    total_plies: int | object
    end_time: str | None
    initial_sfen: str | object | None
    time_control_black: str | object | None
    time_control_white: str | object | None


class GamesListResponse(TypedDict):
    """``get_games`` が返す対局一覧レスポンス。"""

    games: list[TournamentGameEntry]
    total: int
    offset: int
    limit: int


class MatchHistoryResponse(TypedDict):
    """``get_match_history`` が返す対局履歴レスポンス。"""

    games: list[TournamentGameEntry]
    limit: int
    offset: int
    total: int
    signature: str
    source: str
    fetched_at: str


# ---------------------------------------------------------------------------
# Pair stats types
# ---------------------------------------------------------------------------


class PairStatsEntry(TypedDict):
    """ペア統計エントリ。``get_pair_stats`` が返す。"""

    pair_id: str
    engines: list[str]
    games: int
    wins: dict[str, int]
    draws: int
    win_rate: dict[str, float | None]
    los: dict[str, float | None]


class PairStatsResponse(TypedDict):
    """``get_pair_stats`` が返すペア統計レスポンス。"""

    pairs: list[PairStatsEntry]
    total_pairs: int
    signature: str
    source: str
    fetched_at: str


# ---------------------------------------------------------------------------
# Engine options types
# ---------------------------------------------------------------------------


class EngineOptionsResponse(TypedDict):
    """``get_engine_options`` が返すエンジンオプションレスポンス。"""

    engine: str
    options: dict[str, object]
    info: dict[str, str]
    updated_at: str
