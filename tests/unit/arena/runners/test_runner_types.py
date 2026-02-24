"""Arena ランナー層 TypedDict 定義のテスト。

SpsaAlgorithmConfig, _CompletedGameSummary, および runners/types.py の
構造的整合性を検証する。
"""

from __future__ import annotations

from shogiarena.arena.runners.spsa_runner import SpsaAlgorithmConfig
from shogiarena.arena.runners.tournament_runner import _CompletedGameSummary
from shogiarena.arena.runners.types import (
    BtdRatingEntry,
    BtdSummary,
    CancelledGameEntry,
    EngineStatsEntry,
    RunStateConfig,
    SeedSummaryPayload,
    TournamentRunState,
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
# SpsaAlgorithmConfig
# ---------------------------------------------------------------------------


class TestSpsaAlgorithmConfig:
    def test_all_required(self) -> None:
        assert _required_keys(SpsaAlgorithmConfig) == {
            "num_updates",
            "mobility",
            "scale",
            "a0",
            "A",
            "alpha",
            "gamma",
            "crn_enabled",
            "int_rounding",
            "int_ck_floor",
            "update_mode",
            "snap_float_to_step",
            "early_stop",
            "update_batch_size",
            "inflight_factor",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SpsaAlgorithmConfig) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(SpsaAlgorithmConfig)) == 15


# ---------------------------------------------------------------------------
# _CompletedGameSummary
# ---------------------------------------------------------------------------


class TestCompletedGameSummary:
    def test_all_optional(self) -> None:
        assert _required_keys(_CompletedGameSummary) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(_CompletedGameSummary) == {
            "result_code",
            "result_abbr",
            "result_label",
            "result_detail",
            "total_plies",
            "start_time",
            "end_time",
        }

    def test_key_count(self) -> None:
        assert len(_all_keys(_CompletedGameSummary)) == 7


# ---------------------------------------------------------------------------
# EngineStatsEntry
# ---------------------------------------------------------------------------


class TestEngineStatsEntry:
    def test_all_required(self) -> None:
        assert _required_keys(EngineStatsEntry) == {"wins", "losses", "draws", "games"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(EngineStatsEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(EngineStatsEntry)) == 4


# ---------------------------------------------------------------------------
# BtdRatingEntry
# ---------------------------------------------------------------------------


class TestBtdRatingEntry:
    def test_all_required(self) -> None:
        assert _required_keys(BtdRatingEntry) == {"elo", "se"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(BtdRatingEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(BtdRatingEntry)) == 2


# ---------------------------------------------------------------------------
# BtdSummary
# ---------------------------------------------------------------------------


class TestBtdSummary:
    def test_all_optional(self) -> None:
        assert _required_keys(BtdSummary) == set()

    def test_optional_keys(self) -> None:
        assert _optional_keys(BtdSummary) == {
            "ratings",
            "anchor",
            "gamma_elo",
            "gamma_elo_se",
            "draw_eq",
            "draw_eq_se",
            "rating_cov",
            "enginesMeta",
        }

    def test_key_count(self) -> None:
        assert len(_all_keys(BtdSummary)) == 8


# ---------------------------------------------------------------------------
# SeedSummaryPayload
# ---------------------------------------------------------------------------


class TestSeedSummaryPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(SeedSummaryPayload) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(SeedSummaryPayload)) == 27

    def test_contains_common_keys(self) -> None:
        keys = _all_keys(SeedSummaryPayload)
        for k in ("tournamentType", "mode", "engines", "engineStats", "btd", "timestamp"):
            assert k in keys


# ---------------------------------------------------------------------------
# RunStateConfig
# ---------------------------------------------------------------------------


class TestRunStateConfig:
    def test_all_optional(self) -> None:
        assert _required_keys(RunStateConfig) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(RunStateConfig)) == 7


# ---------------------------------------------------------------------------
# CancelledGameEntry
# ---------------------------------------------------------------------------


class TestCancelledGameEntry:
    def test_all_optional(self) -> None:
        assert _required_keys(CancelledGameEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(CancelledGameEntry)) == 8


# ---------------------------------------------------------------------------
# TournamentRunState
# ---------------------------------------------------------------------------


class TestTournamentRunState:
    def test_all_optional(self) -> None:
        assert _required_keys(TournamentRunState) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(TournamentRunState)) == 17

    def test_contains_core_keys(self) -> None:
        keys = _all_keys(TournamentRunState)
        for k in ("config", "total_games", "completed_game_ids", "finished"):
            assert k in keys
