"""トーナメントダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from typing import get_type_hints

from shogiarena.web.dashboard.backend.tournament.types import (
    EngineOptionsResponse,
    GamesCounter,
    GamesListResponse,
    MatchHistoryResponse,
    PairStatsEntry,
    PairStatsResponse,
    ProgressPayload,
    StandingEntry,
    StandingsPayload,
    TournamentGameEntry,
)


def _required_keys(td: type) -> set[str]:
    """TypedDict の必須キーを返す。"""
    return set(td.__required_keys__)


def _optional_keys(td: type) -> set[str]:
    """TypedDict のオプショナルキーを返す。"""
    return set(td.__optional_keys__)


def _all_keys(td: type) -> set[str]:
    """TypedDict の全キーを返す。"""
    return _required_keys(td) | _optional_keys(td)


# ---------------------------------------------------------------------------
# Standings types
# ---------------------------------------------------------------------------


class TestStandingEntry:
    def test_all_required(self) -> None:
        expected = {"engine", "points", "games", "wins", "draws", "losses", "win_rate", "rating", "rank"}
        assert _required_keys(StandingEntry) == expected

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(StandingEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(StandingEntry)) == 9


class TestStandingsPayload:
    def test_all_required(self) -> None:
        expected = {"standings", "enginesMeta", "updated_at"}
        assert _required_keys(StandingsPayload) == expected

    def test_references_standing_entry(self) -> None:
        hints = get_type_hints(StandingsPayload)
        assert "standings" in hints


# ---------------------------------------------------------------------------
# Progress types
# ---------------------------------------------------------------------------


class TestGamesCounter:
    def test_all_required(self) -> None:
        assert _required_keys(GamesCounter) == {"completed", "total", "cancelled"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(GamesCounter) == set()


class TestProgressPayload:
    def test_all_required(self) -> None:
        expected = {"games", "inProgress", "pending", "completionRate", "estimatedTimeRemaining", "updatedAt"}
        assert _required_keys(ProgressPayload) == expected

    def test_references_games_counter(self) -> None:
        hints = get_type_hints(ProgressPayload)
        assert "games" in hints


# ---------------------------------------------------------------------------
# Game entry types
# ---------------------------------------------------------------------------


class TestTournamentGameEntry:
    def test_all_optional(self) -> None:
        assert _required_keys(TournamentGameEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(TournamentGameEntry)) == 9

    def test_contains_player_fields(self) -> None:
        keys = _all_keys(TournamentGameEntry)
        assert {"game_id", "black_player", "white_player", "result_code"} <= keys

    def test_contains_time_control_fields(self) -> None:
        keys = _all_keys(TournamentGameEntry)
        assert {"time_control_black", "time_control_white"} <= keys


class TestGamesListResponse:
    def test_all_required(self) -> None:
        expected = {"games", "total", "offset", "limit"}
        assert _required_keys(GamesListResponse) == expected

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(GamesListResponse) == set()


class TestMatchHistoryResponse:
    def test_all_required(self) -> None:
        expected = {"games", "limit", "offset", "total", "signature", "source", "fetched_at"}
        assert _required_keys(MatchHistoryResponse) == expected

    def test_key_count(self) -> None:
        assert len(_all_keys(MatchHistoryResponse)) == 7


# ---------------------------------------------------------------------------
# Pair stats types
# ---------------------------------------------------------------------------


class TestPairStatsEntry:
    def test_all_required(self) -> None:
        expected = {"pair_id", "engines", "games", "wins", "draws", "win_rate", "los"}
        assert _required_keys(PairStatsEntry) == expected

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(PairStatsEntry) == set()


class TestPairStatsResponse:
    def test_all_required(self) -> None:
        expected = {"pairs", "total_pairs", "signature", "source", "fetched_at"}
        assert _required_keys(PairStatsResponse) == expected


# ---------------------------------------------------------------------------
# Engine options types
# ---------------------------------------------------------------------------


class TestEngineOptionsResponse:
    def test_all_required(self) -> None:
        expected = {"engine", "options", "info", "updated_at"}
        assert _required_keys(EngineOptionsResponse) == expected

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(EngineOptionsResponse) == set()
