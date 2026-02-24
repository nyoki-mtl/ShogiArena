"""オーケストレータ diff ペイロード TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

from shogiarena.arena.orchestrators.base_orchestrator_utils import (
    ClockIncrementDiffPayload,
    ClockStartDiffPayload,
    GameAssignedDiffPayload,
    HandshakeDiffPayload,
    MoveDiffPayload,
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
# Handshake
# ---------------------------------------------------------------------------


class TestHandshakeDiffPayload:
    def test_required_keys(self) -> None:
        assert _required_keys(HandshakeDiffPayload) == {"engine_status"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(HandshakeDiffPayload) == set()


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------


class TestMoveDiffPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(MoveDiffPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(MoveDiffPayload) == {
            "game_id",
            "initial_sfen",
            "black_name",
            "white_name",
            "currentPly",
            "move",
            "ki2_move",
            "eval",
            "sfen",
            "depth",
            "seldepth",
            "nodes",
            "time_ms",
            "wall_time_ms",
            "latency_ms",
            "latency_alert",
            "result_code",
            "engine_status",
        }


# ---------------------------------------------------------------------------
# Clock Start
# ---------------------------------------------------------------------------


class TestClockStartDiffPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(ClockStartDiffPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(ClockStartDiffPayload) == {
            "type",
            "game_id",
            "active",
            "black_remain_ms",
            "white_remain_ms",
            "started_at_ms",
            "initial_sfen",
            "black_name",
            "white_name",
            "time_control_black",
            "time_control_white",
            "byoyomi_ms_black",
            "byoyomi_ms_white",
            "increment_ms_black",
            "increment_ms_white",
        }


# ---------------------------------------------------------------------------
# Clock Increment
# ---------------------------------------------------------------------------


class TestClockIncrementDiffPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(ClockIncrementDiffPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(ClockIncrementDiffPayload) == {
            "type",
            "game_id",
            "side",
            "applied_increment_ms",
            "pre_black_remain_ms",
            "pre_white_remain_ms",
            "black_remain_ms",
            "white_remain_ms",
            "occurred_at_ms",
        }


# ---------------------------------------------------------------------------
# Game Assigned
# ---------------------------------------------------------------------------


class TestGameAssignedDiffPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(GameAssignedDiffPayload) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(GameAssignedDiffPayload) == {
            "type",
            "game_id",
            "initial_sfen",
            "black_name",
            "white_name",
            "engine_status",
            "time_control_black",
            "time_control_white",
        }


# ---------------------------------------------------------------------------
# Cross-type consistency
# ---------------------------------------------------------------------------


class TestCrossTypeConsistency:
    """diff ペイロード間の構造的一貫性を検証する。"""

    def test_clock_types_share_common_keys(self) -> None:
        """ClockStart と ClockIncrement は共通キーを持つ。"""
        start_keys = _all_keys(ClockStartDiffPayload)
        inc_keys = _all_keys(ClockIncrementDiffPayload)
        common = start_keys & inc_keys
        assert {"type", "game_id", "black_remain_ms", "white_remain_ms"} <= common

    def test_game_assigned_subset_of_clock_start(self) -> None:
        """GameAssigned の game_id, black_name, white_name は ClockStart にも存在する。"""
        assigned_keys = _all_keys(GameAssignedDiffPayload)
        start_keys = _all_keys(ClockStartDiffPayload)
        shared_identity = {"game_id", "black_name", "white_name"}
        assert shared_identity <= assigned_keys
        assert shared_identity <= start_keys
