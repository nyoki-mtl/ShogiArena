"""トーナメントダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.tournament.payloads import (
    GamesCounter,
    HeadToHeadPayload,
    PairStatsEntry,
    PairStatsPayload,
    ProgressPayload,
    StandingEntry,
    StandingsPayload,
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
        expected = {"standings", "engines_meta", "updated_at"}
        assert _required_keys(StandingsPayload) == expected

    def test_references_standing_entry(self) -> None:
        assert "standings" in StandingsPayload.__annotations__


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
        expected = {"games", "in_progress", "pending", "completion_rate", "estimated_time_remaining", "updated_at"}
        assert _required_keys(ProgressPayload) == expected

    def test_references_games_counter(self) -> None:
        assert "games" in ProgressPayload.__annotations__


class TestHeadToHeadPayload:
    def test_all_required(self) -> None:
        assert _required_keys(HeadToHeadPayload) == {"head_to_head", "updated_at"}


class TestPairStatsEntry:
    def test_all_required(self) -> None:
        expected = {"pair_id", "engines", "games", "wins", "draws", "win_rate", "los"}
        assert _required_keys(PairStatsEntry) == expected

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(PairStatsEntry) == set()


class TestPairStatsPayload:
    def test_all_required(self) -> None:
        expected = {"pairs", "total_pairs", "signature", "source", "fetched_at"}
        assert _required_keys(PairStatsPayload) == expected
