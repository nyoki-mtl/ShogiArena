"""マッチ・SPRT ダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from shogiarena.web.dashboard.backend.match.types import (
    ColorCounts,
    ColorTimelineEntry,
    ConfidenceInterval,
    MatchGames,
    MatchPayload,
    MatchTimelineEntry,
    WdlGamesCount,
)
from shogiarena.web.dashboard.backend.sprt.types import (
    SprtConfig,
    SprtGames,
    SprtPayload,
    SprtStatus,
    SprtTimelineEntry,
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
# Match types
# ---------------------------------------------------------------------------


class TestConfidenceInterval:
    def test_required_keys(self) -> None:
        assert _required_keys(ConfidenceInterval) == {"lower", "upper"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(ConfidenceInterval) == set()


class TestWdlGamesCount:
    def test_required_keys(self) -> None:
        assert _required_keys(WdlGamesCount) == {"wins", "losses", "draws", "games"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(WdlGamesCount) == set()


class TestColorCounts:
    def test_required_keys(self) -> None:
        assert _required_keys(ColorCounts) == {"black", "white"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(ColorCounts) == set()


class TestMatchGames:
    def test_required_keys(self) -> None:
        assert _required_keys(MatchGames) == {"completed", "total", "wins", "losses", "draws"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(MatchGames) == set()


class TestColorTimelineEntry:
    def test_required_keys(self) -> None:
        assert _required_keys(ColorTimelineEntry) == {
            "wins",
            "losses",
            "draws",
            "games",
            "winRate",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(ColorTimelineEntry) == set()


class TestMatchTimelineEntry:
    def test_required_keys(self) -> None:
        assert _required_keys(MatchTimelineEntry) == {
            "gameIndex",
            "wins",
            "losses",
            "draws",
            "games",
            "winRate",
            "eloEstimate",
            "black",
            "white",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(MatchTimelineEntry) == set()


class TestMatchPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(MatchPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(MatchPayload) == {
            "mode",
            "summarySource",
            "tested",
            "baseline",
            "games",
            "winRate",
            "winRateCi95",
            "eloEstimate",
            "eloCi95",
            "colors",
            "timeline",
            "timestamp",
            "liveView",
        }


# ---------------------------------------------------------------------------
# SPRT types
# ---------------------------------------------------------------------------


class TestSprtConfig:
    def test_required_keys(self) -> None:
        assert _required_keys(SprtConfig) == {
            "elo0",
            "elo1",
            "alpha",
            "beta",
            "minGames",
            "maxGames",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SprtConfig) == set()


class TestSprtStatus:
    def test_required_keys(self) -> None:
        assert _required_keys(SprtStatus) == {
            "llr",
            "lower",
            "upper",
            "decision",
            "wins",
            "draws",
            "losses",
            "games",
            "winRate",
            "eloEstimate",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SprtStatus) == set()


class TestSprtGames:
    def test_required_keys(self) -> None:
        assert _required_keys(SprtGames) == {"completed", "total"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SprtGames) == set()


class TestSprtTimelineEntry:
    def test_required_keys(self) -> None:
        assert _required_keys(SprtTimelineEntry) == {
            "gameIndex",
            "llr",
            "lower",
            "upper",
            "decision",
            "wins",
            "draws",
            "losses",
            "games",
            "winRate",
            "eloEstimate",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SprtTimelineEntry) == set()


class TestSprtPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(SprtPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(SprtPayload) == {
            "mode",
            "summarySource",
            "tested",
            "baseline",
            "config",
            "status",
            "games",
            "timeline",
            "timestamp",
            "liveView",
        }


# ---------------------------------------------------------------------------
# Cross-type consistency
# ---------------------------------------------------------------------------


class TestCrossTypeConsistency:
    """型間の構造的一貫性を検証する。"""

    def test_sprt_timeline_superset_of_status(self) -> None:
        """SprtTimelineEntry は SprtStatus の全キーを含む（+ gameIndex）。"""
        status_keys = _all_keys(SprtStatus)
        timeline_keys = _all_keys(SprtTimelineEntry)
        assert status_keys < timeline_keys
        assert timeline_keys - status_keys == {"gameIndex"}

    def test_match_timeline_color_entry_consistency(self) -> None:
        """MatchTimelineEntry は ColorTimelineEntry のキーを含む。"""
        color_keys = _all_keys(ColorTimelineEntry)
        timeline_keys = _all_keys(MatchTimelineEntry)
        assert color_keys < timeline_keys
