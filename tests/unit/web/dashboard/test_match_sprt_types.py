"""マッチ・SPRT ダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from shogiarena._core.interfaces.dashboard.match.payloads import (
    ColorTimelineEntry,
    ConfidenceInterval,
    MatchTimelineEntry,
    WdlGamesCount,
)
from shogiarena._core.interfaces.dashboard.sprt.payloads import (
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


# ---------------------------------------------------------------------------
# SPRT types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Cross-type consistency
# ---------------------------------------------------------------------------


class TestCrossTypeConsistency:
    """型間の構造的一貫性を検証する。"""

    def test_match_timeline_color_entry_consistency(self) -> None:
        """MatchTimelineEntry は ColorTimelineEntry のキーを含む。"""
        color_keys = _all_keys(ColorTimelineEntry)
        timeline_keys = _all_keys(MatchTimelineEntry)
        assert color_keys < timeline_keys
