"""ランナー層の大型ペイロード TypedDict 定義。

``seed_initial_summary()`` や ``_save_run_state()`` が構築する巨大 dict の
構造をドキュメントとして型で表現する。ミューテーションが多いため戻り値型には
直接適用せず、構造の自己文書化に利用する。
"""

from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# seed_initial_summary() 共通構造
# ---------------------------------------------------------------------------


class EngineStatsEntry(TypedDict):
    """エンジンごとの WDL カウント。"""

    wins: int
    losses: int
    draws: int
    games: int


class BtdRatingEntry(TypedDict):
    """BTD レーティングのエンジンごとのエントリ。"""

    elo: float
    se: float


class BtdSummary(TypedDict, total=False):
    """Bradley-Terry-Davidson サマリ。"""

    ratings: dict[str, BtdRatingEntry]
    anchor: str | None
    gamma_elo: float
    gamma_elo_se: float
    draw_eq: float
    draw_eq_se: float
    rating_cov: dict[str, dict[str, float]]
    enginesMeta: list[dict[str, object]]


class SeedSummaryPayload(TypedDict, total=False):
    """``seed_initial_summary()`` が構築するサマリペイロード（SPSA/Tournament 共通）。"""

    tournamentType: str
    mode: str
    flipPolicy: object
    numEngines: int
    runDir: str
    leaderboard: list[object]
    ratingInitial: int | float
    engines: list[str]
    enginesMeta: list[dict[str, object]]
    engineTimeControls: dict[str, str]
    defaultTimeControl: str | None
    engineInstances: dict[str, str | None]
    engineStats: dict[str, EngineStatsEntry]
    pairResults: dict[str, object]
    timestamp: str
    games: dict[str, int]
    btd: BtdSummary
    # Conditional keys (tournament-specific)
    rules: dict[str, object]
    initialPositions: object
    repetitionOccurrencesToDraw: object
    tournamentConfig: dict[str, object]
    sprt: dict[str, object]
    spsaConfig: dict[str, object]
    generateConfig: dict[str, object]
    recordsOutput: dict[str, object]
    summaryReady: bool
    liveView: dict[str, object]


# ---------------------------------------------------------------------------
# _save_run_state() 構造
# ---------------------------------------------------------------------------


class RunStateConfig(TypedDict, total=False):
    """``run_state["config"]`` のネスト構造。"""

    experiment_name: str | None
    engines: list[str]
    tournament: dict[str, object]
    rules: dict[str, object]
    sprt: dict[str, object]
    openbench: dict[str, object]
    records_output: dict[str, object]


class CancelledGameEntry(TypedDict, total=False):
    """``run_state["cancelled_games"]`` の各エントリ。"""

    game_id: str
    black: str
    white: str
    round: int
    sfen: str
    assignment: dict[str, object]
    assigned_instance: str
    require_install: bool


class TournamentRunState(TypedDict, total=False):
    """``_save_run_state()`` が構築する永続化ペイロード。"""

    config: RunStateConfig
    schedule_hash: str
    total_games: int
    completed_game_ids: list[str]
    cancelled_game_ids: list[str]
    cancelled_games: list[CancelledGameEntry]
    original_total_games: int
    game_display_order: dict[str, int]
    finished: bool
    created_at: str
    updated_at: str
    sprt_state: dict[str, object]
    openbench_state: dict[str, object]
    completed_game_summaries: dict[str, dict[str, object]]
    game_instance_overrides: dict[str, dict[str, object]]
    # Generate mode variant
    completed_games_count: int
    cancelled_games_count: int
