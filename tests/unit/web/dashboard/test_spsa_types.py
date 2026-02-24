"""SPSA ダッシュボードバックエンド TypedDict 定義のテスト。

各 TypedDict の構造的整合性（キーセット、Required/NotRequired）を検証する。
"""

from __future__ import annotations

import sys
from typing import get_type_hints

import pytest

from shogiarena.web.dashboard.backend.spsa.types import (
    ConvergenceAnalysis,
    ConvergenceMetrics,
    ConvergencePrediction,
    CorrelationAnalysis,
    GameBriefEntry,
    GameListEntry,
    LtcBestEstimate,
    LtcRegressionDetail,
    LtcSummary,
    MobilitySeriesPayload,
    ParamEntry,
    ParameterTimelineEntry,
    ParamsPayload,
    ProgressSnapshot,
    SpsaSummaryGames,
    SpsaSummaryPayload,
    UpdateDetailResponse,
    UpdateEntry,
    WdlCounts,
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
# Foundation types
# ---------------------------------------------------------------------------


class TestWdlCounts:
    def test_required_keys(self) -> None:
        assert _required_keys(WdlCounts) == {"wins", "losses", "draws"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(WdlCounts) == set()


# ---------------------------------------------------------------------------
# Game-related types
# ---------------------------------------------------------------------------


class TestGameBriefEntry:
    def test_required_keys(self) -> None:
        assert _required_keys(GameBriefEntry) == {"game_id"}

    def test_optional_keys_contain_player_fields(self) -> None:
        optional = _optional_keys(GameBriefEntry)
        assert {"black_player", "white_player", "result_code"} <= optional

    def test_all_keys(self) -> None:
        expected = {
            "game_id",
            "black_player",
            "white_player",
            "result_code",
            "num_moves",
            "variant_id",
            "phase",
            "status",
            "assigned_instance",
            "round",
            "start_time",
            "end_time",
        }
        assert _all_keys(GameBriefEntry) == expected


class TestGameListEntry:
    def test_all_optional(self) -> None:
        assert _required_keys(GameListEntry) == set()

    def test_key_count(self) -> None:
        assert len(_all_keys(GameListEntry)) == 12

    def test_has_timestamp(self) -> None:
        assert "timestamp" in _all_keys(GameListEntry)


# ---------------------------------------------------------------------------
# LTC types
# ---------------------------------------------------------------------------


class TestLtcRegressionDetail:
    def test_all_optional(self) -> None:
        assert _required_keys(LtcRegressionDetail) == set()

    def test_contains_sprt_fields(self) -> None:
        keys = _all_keys(LtcRegressionDetail)
        assert {"sprt", "sprt_decision", "status"} <= keys


# ---------------------------------------------------------------------------
# Update types
# ---------------------------------------------------------------------------


class TestUpdateEntry:
    def test_all_optional(self) -> None:
        assert _required_keys(UpdateEntry) == set()

    def test_core_keys_present(self) -> None:
        keys = _all_keys(UpdateEntry)
        assert {"update_idx", "variant_id", "params", "gradients", "deltas"} <= keys

    def test_wdl_keys_present(self) -> None:
        keys = _all_keys(UpdateEntry)
        assert {"wins", "losses", "draws", "phase_wdl"} <= keys

    def test_ltc_keys_present(self) -> None:
        keys = _all_keys(UpdateEntry)
        assert {"ltc_regression", "has_ltc_regression", "ltc_rejected", "ltc_reverted_to"} <= keys


class TestUpdateDetailResponse:
    def test_all_optional(self) -> None:
        assert _required_keys(UpdateDetailResponse) == set()

    def test_contains_payload_key(self) -> None:
        assert "payload" in _all_keys(UpdateDetailResponse)


# ---------------------------------------------------------------------------
# Progress / Timeline types
# ---------------------------------------------------------------------------


class TestProgressSnapshot:
    def test_all_required(self) -> None:
        assert _required_keys(ProgressSnapshot) == {"completed", "total", "percent"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(ProgressSnapshot) == set()


class TestParameterTimelineEntry:
    def test_all_required(self) -> None:
        expected = {"update_idx", "actual", "baseline", "pending", "ltc_invalidated", "ltc_decision"}
        assert _required_keys(ParameterTimelineEntry) == expected


class TestMobilitySeriesPayload:
    def test_all_required(self) -> None:
        assert _required_keys(MobilitySeriesPayload) == {"gain_ak", "variant_indices"}


class TestCorrelationAnalysis:
    def test_all_optional(self) -> None:
        assert _required_keys(CorrelationAnalysis) == set()

    def test_contains_correlation_keys(self) -> None:
        keys = _all_keys(CorrelationAnalysis)
        assert {"correlations", "parameter_evolution", "gradient_evolution"} <= keys


class TestConvergenceMetrics:
    def test_all_optional(self) -> None:
        assert _required_keys(ConvergenceMetrics) == set()

    def test_contains_convergence_indicator(self) -> None:
        assert "is_converging" in _all_keys(ConvergenceMetrics)


class TestConvergencePrediction:
    def test_all_optional(self) -> None:
        assert _required_keys(ConvergencePrediction) == set()

    def test_keys(self) -> None:
        assert _all_keys(ConvergencePrediction) == {"remaining_updates_estimate", "convergence_probability"}


class TestConvergenceAnalysis:
    def test_all_optional(self) -> None:
        assert _required_keys(ConvergenceAnalysis) == set()

    def test_contains_nested_type_keys(self) -> None:
        keys = _all_keys(ConvergenceAnalysis)
        assert {"convergence_metrics", "prediction", "mobility_series"} <= keys

    def test_contains_ltc_results(self) -> None:
        assert "ltc_results" in _all_keys(ConvergenceAnalysis)


# ---------------------------------------------------------------------------
# Summary types
# ---------------------------------------------------------------------------


class TestSpsaSummaryGames:
    def test_all_required(self) -> None:
        assert _required_keys(SpsaSummaryGames) == {"completed", "total"}


class TestSpsaSummaryPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(SpsaSummaryPayload) == set()

    def test_contains_engine_keys(self) -> None:
        keys = _all_keys(SpsaSummaryPayload)
        assert {"engines", "enginesMeta", "engineInstances", "engineStats"} <= keys

    def test_key_count_minimum(self) -> None:
        assert len(_all_keys(SpsaSummaryPayload)) >= 25


# ---------------------------------------------------------------------------
# Params types
# ---------------------------------------------------------------------------


class TestParamEntry:
    def test_all_required(self) -> None:
        expected = {"name", "type", "v", "min", "max", "step", "delta", "comment", "not_used"}
        assert _required_keys(ParamEntry) == expected


class TestParamsPayload:
    def test_all_optional(self) -> None:
        assert _required_keys(ParamsPayload) == set()

    def test_contains_params_key(self) -> None:
        assert "params" in _all_keys(ParamsPayload)


# ---------------------------------------------------------------------------
# LTC summary types
# ---------------------------------------------------------------------------


class TestLtcBestEstimate:
    def test_all_optional(self) -> None:
        assert _required_keys(LtcBestEstimate) == set()

    def test_contains_statistical_fields(self) -> None:
        keys = _all_keys(LtcBestEstimate)
        assert {"mean", "variance", "sigma", "lower", "upper"} <= keys


class TestLtcSummary:
    def test_all_optional(self) -> None:
        assert _required_keys(LtcSummary) == set()

    def test_contains_core_fields(self) -> None:
        keys = _all_keys(LtcSummary)
        assert {"enabled", "status", "config", "best_estimate"} <= keys


# ---------------------------------------------------------------------------
# Cross-type consistency
# ---------------------------------------------------------------------------


class TestCrossTypeConsistency:
    def test_update_entry_references_wdl_counts(self) -> None:
        """UpdateEntry の phase_wdl が WdlCounts を参照している。"""
        hints = get_type_hints(UpdateEntry)
        # phase_wdl: dict[str, WdlCounts]
        assert "phase_wdl" in hints

    def test_convergence_analysis_references_metrics(self) -> None:
        """ConvergenceAnalysis が ConvergenceMetrics を参照している。"""
        hints = get_type_hints(ConvergenceAnalysis)
        assert "convergence_metrics" in hints

    def test_convergence_analysis_references_prediction(self) -> None:
        """ConvergenceAnalysis が ConvergencePrediction を参照している。"""
        hints = get_type_hints(ConvergenceAnalysis)
        assert "prediction" in hints

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="get_type_hints behavior varies")
    def test_params_payload_references_param_entry(self) -> None:
        """ParamsPayload が ParamEntry を参照している。"""
        hints = get_type_hints(ParamsPayload)
        assert "params" in hints
