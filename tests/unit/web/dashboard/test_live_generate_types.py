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
            "unit_label",
            "completed",
            "total",
            "cancelled",
            "is_final",
            "state",
            "updated_at",
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
            "total_games",
            "total_positions",
            "total_bytes",
            "file_count",
            "run_dir",
            "tournament_type",
            "mode",
            "record_format",
            "output_dir",
            "file_prefix",
            "rules",
            "run_status",
            "is_resumable",
        }
