"""Arena configs 層 TypedDict 定義のテスト。

_WdlCounts および _EngineWdlCounts の構造的整合性を検証する。
"""

from __future__ import annotations

from shogiarena._core.contexts.game_session.domain.summary_models import EngineWdlCounts as _EngineWdlCounts
from shogiarena._core.contexts.game_session.domain.summary_models import WdlCounts as _WdlCounts


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
# _WdlCounts
# ---------------------------------------------------------------------------


class TestWdlCounts:
    def test_all_required(self) -> None:
        assert _required_keys(_WdlCounts) == {"wins", "draws", "losses"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(_WdlCounts) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(_WdlCounts)) == 3


# ---------------------------------------------------------------------------
# _EngineWdlCounts
# ---------------------------------------------------------------------------


class TestEngineWdlCounts:
    def test_required_keys(self) -> None:
        assert _required_keys(_EngineWdlCounts) == {"wins", "draws", "losses"}

    def test_optional_keys(self) -> None:
        assert _optional_keys(_EngineWdlCounts) == {"games"}

    def test_key_count(self) -> None:
        assert len(_all_keys(_EngineWdlCounts)) == 4

    def test_inherits_from_wdl_counts(self) -> None:
        assert _required_keys(_WdlCounts).issubset(_all_keys(_EngineWdlCounts))
