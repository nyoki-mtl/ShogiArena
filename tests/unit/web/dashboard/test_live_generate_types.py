"""ライブビュー・生成モード ダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.live.view_payloads import (
    LiveViewProgress,
    LiveViewSnapshot,
)
from shogiarena._core.interfaces.dashboard.generate.payloads import GenerateSummary


def _required_keys(td: type) -> set[str]:
    """TypedDict の必須キーを返す。"""
    return set(td.__required_keys__)


def _optional_keys(td: type) -> set[str]:
    """TypedDict のオプショナルキーを返す。"""
    return set(td.__optional_keys__)


# ---------------------------------------------------------------------------
# Live view types
# ---------------------------------------------------------------------------


class TestLiveViewProgress:
    def test_all_optional(self) -> None:
        assert _required_keys(LiveViewProgress) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(LiveViewProgress) == {
            "kind",
            "unitLabel",
            "completed",
            "total",
            "cancelled",
            "isFinal",
            "state",
            "updatedAt",
        }


class TestLiveViewSnapshot:
    def test_required_keys(self) -> None:
        assert _required_keys(LiveViewSnapshot) == {"version", "mode", "progress"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(LiveViewSnapshot) == set()


# ---------------------------------------------------------------------------
# Generate types
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    def test_all_optional(self) -> None:
        assert _required_keys(GenerateSummary) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(GenerateSummary) == {
            "totalGames",
            "totalPositions",
            "totalBytes",
            "fileCount",
            "runDir",
            "tournamentType",
            "mode",
            "recordFormat",
            "outputDir",
            "filePrefix",
            "rules",
        }
