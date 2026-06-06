"""SPSA ダッシュボード Pydantic 境界モデルのテスト。

各モデルの正常パース、欠落フィールドでの安全デフォルト、
camelCase 正規化、型強制、プロパティ/メソッドを検証する。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from shogiarena._core.contexts.dashboard.application.spsa.io_models.index_io_models import (
    IndexData,
    IndexMetadata,
    LtcRegressionIndexInfo,
    RawIndexUpdateEntry,
)
from shogiarena._core.contexts.dashboard.application.spsa.io_models.meta_io_models import (
    EngineStatEntry,
    LtcRegressionMetaConfig,
    SpsaMetaData,
)
from shogiarena._core.contexts.spsa.application.dashboard.summary_cache_payload import SummaryCachePayload

# ---------------------------------------------------------------------------
# EngineStatEntry
# ---------------------------------------------------------------------------


class TestEngineStatEntry:
    def test_normal(self) -> None:
        e = EngineStatEntry.model_validate({"wins": 10, "losses": 5, "draws": 3, "games": 18})
        assert e.wins == 10
        assert e.losses == 5
        assert e.draws == 3
        assert e.games == 18

    def test_defaults(self) -> None:
        e = EngineStatEntry.model_validate({})
        assert e.wins == 0
        assert e.losses == 0
        assert e.draws == 0
        assert e.games == 0

    def test_extra_ignored(self) -> None:
        e = EngineStatEntry.model_validate({"wins": 1, "unknown_field": "hello"})
        assert e.wins == 1


# ---------------------------------------------------------------------------
# LtcRegressionMetaConfig
# ---------------------------------------------------------------------------


class TestLtcRegressionMetaConfig:
    def test_normal(self) -> None:
        c = LtcRegressionMetaConfig.model_validate({"enabled": True, "pairs": 100, "sprt_elo0": 0.0, "sprt_elo1": 5.0})
        assert c.is_enabled is True
        assert c.pairs == 100
        assert c.sprt_elo0 == 0.0
        assert c.sprt_elo1 == 5.0

    def test_defaults(self) -> None:
        c = LtcRegressionMetaConfig.model_validate({})
        assert c.is_enabled is False
        assert c.pairs is None

    def test_enabled_coercion_from_int(self) -> None:
        c = LtcRegressionMetaConfig.model_validate({"enabled": 1})
        assert c.is_enabled is True
        c2 = LtcRegressionMetaConfig.model_validate({"enabled": 0})
        assert c2.is_enabled is False

    def test_enabled_coercion_from_string(self) -> None:
        c = LtcRegressionMetaConfig.model_validate({"enabled": "yes"})
        assert c.is_enabled is False  # non-bool/non-int → False


# ---------------------------------------------------------------------------
# LtcRegressionIndexInfo
# ---------------------------------------------------------------------------


class TestLtcRegressionIndexInfo:
    def test_normal(self) -> None:
        info = LtcRegressionIndexInfo.model_validate(
            {
                "status": "running",
                "last_update_idx": 50,
                "winrate": 0.55,
                "elo": 12.3,
                "pairs_played": 200,
                "sprt": {"llr": 1.5, "threshold": 2.94},
                "sprt_decision": "H1",
                "is_accepted": True,
            }
        )
        assert info.status == "running"
        assert info.winrate == 0.55
        assert info.sprt == {"llr": 1.5, "threshold": 2.94}
        assert info.is_accepted is True

    def test_legacy_accepted_key_is_ignored(self) -> None:
        info = LtcRegressionIndexInfo.model_validate({"accepted": True})
        assert info.is_accepted is None

    def test_defaults(self) -> None:
        info = LtcRegressionIndexInfo.model_validate({})
        assert info.status is None
        assert info.sprt is None

    def test_sprt_non_dict_coerced_to_none(self) -> None:
        info = LtcRegressionIndexInfo.model_validate({"sprt": "invalid"})
        assert info.sprt is None

    def test_sprt_list_coerced_to_none(self) -> None:
        info = LtcRegressionIndexInfo.model_validate({"sprt": [1, 2, 3]})
        assert info.sprt is None


# ---------------------------------------------------------------------------
# SpsaMetaData
# ---------------------------------------------------------------------------


class TestSpsaMetaData:
    """meta.json パース用モデルのテスト。"""

    @pytest.fixture
    def full_meta(self) -> dict[str, Any]:
        return {
            "session_uuid": "abc-123",
            "num_updates": 500,
            "experiment_name": "test_exp",
            "initial_params": {"param_a": 1.5, "param_b": 2.0},
            "space_path": "/path/to/space.yaml",
            "engine_time_controls": {"engine1": "1000+10"},
            "default_time_control": "1000+10",
            "engines": ["engine1", "engine2"],
            "engine_instances": {"engine1": "inst_a", "engine2": None},
            "engine_stats": {"engine1": {"wins": 10, "losses": 5, "draws": 3, "games": 18}},
            "engines_meta": [{"name": "engine1", "version": "1.0"}],
            "mobility": 0.5,
            "scale": 1.0,
            "a0": 100.0,
            "A": 50.0,
            "alpha": 0.602,
            "gamma": 0.101,
            "crn_enabled": True,
            "int_rounding": "nearest",
        }

    def test_full_parse(self, full_meta: dict[str, Any]) -> None:
        m = SpsaMetaData.model_validate(full_meta)
        assert m.session_uuid == "abc-123"
        assert m.num_updates == 500
        assert m.experiment_name == "test_exp"
        assert m.initial_params == {"param_a": 1.5, "param_b": 2.0}
        assert m.engines == ["engine1", "engine2"]
        assert m.engine_instances == {"engine1": "inst_a", "engine2": None}
        assert isinstance(m.engine_stats["engine1"], EngineStatEntry)
        assert m.engine_stats["engine1"].wins == 10
        assert m.spsa_A == 50.0
        assert m.mobility == 0.5

    def test_empty_dict(self) -> None:
        m = SpsaMetaData.model_validate({})
        assert m.session_uuid is None
        assert m.num_updates is None
        assert m.initial_params == {}
        assert m.engines == []
        assert m.engine_stats == {}

    # -- camelCase normalization --

    def test_camel_case_normalization(self) -> None:
        data = {
            "engineTimeControls": {"e1": "500+5"},
            "defaultTimeControl": "500+5",
            "engineInstances": {"e1": "inst"},
            "engineStats": {"e1": {"wins": 1}},
            "enginesMeta": [{"name": "e1"}],
        }
        m = SpsaMetaData.model_validate(data)
        assert m.engine_time_controls == {"e1": "500+5"}
        assert m.default_time_control == "500+5"
        assert m.engine_instances == {"e1": "inst"}
        assert m.engine_stats["e1"].wins == 1
        assert m.engines_meta == [{"name": "e1"}]

    def test_snake_case_takes_precedence(self) -> None:
        """snake_case が既にある場合は camelCase で上書きしない。"""
        data = {
            "engine_time_controls": {"e1": "1000+10"},
            "engineTimeControls": {"e1": "500+5"},
        }
        m = SpsaMetaData.model_validate(data)
        assert m.engine_time_controls == {"e1": "1000+10"}

    # -- session_uuid coercion --

    def test_session_uuid_stripped(self) -> None:
        m = SpsaMetaData.model_validate({"session_uuid": "  abc  "})
        assert m.session_uuid == "abc"

    def test_session_uuid_empty_to_none(self) -> None:
        m = SpsaMetaData.model_validate({"session_uuid": "   "})
        assert m.session_uuid is None

    def test_session_uuid_non_string_to_none(self) -> None:
        m = SpsaMetaData.model_validate({"session_uuid": 12345})
        assert m.session_uuid is None

    # -- initial_params coercion --

    def test_initial_params_coerces_strings(self) -> None:
        m = SpsaMetaData.model_validate({"initial_params": {"a": "1.5", "b": 2}})
        assert m.initial_params == {"a": 1.5, "b": 2.0}

    def test_initial_params_drops_non_numeric(self) -> None:
        m = SpsaMetaData.model_validate({"initial_params": {"a": "hello", "b": 1.0}})
        assert m.initial_params == {"b": 1.0}

    def test_initial_params_drops_inf_nan(self) -> None:
        m = SpsaMetaData.model_validate({"initial_params": {"a": float("inf"), "b": float("nan"), "c": 1.0}})
        assert m.initial_params == {"c": 1.0}

    def test_initial_params_non_dict(self) -> None:
        m = SpsaMetaData.model_validate({"initial_params": "bad"})
        assert m.initial_params == {}

    # -- engine_time_controls coercion --

    def test_time_controls_none_value(self) -> None:
        m = SpsaMetaData.model_validate({"engine_time_controls": {"e1": None}})
        assert m.engine_time_controls == {"e1": "-"}

    def test_time_controls_empty_key_skipped(self) -> None:
        m = SpsaMetaData.model_validate({"engine_time_controls": {"": "100+1", "e1": "200+2"}})
        assert m.engine_time_controls == {"e1": "200+2"}

    def test_time_controls_non_dict(self) -> None:
        m = SpsaMetaData.model_validate({"engine_time_controls": 42})
        assert m.engine_time_controls == {}

    # -- engines coercion --

    def test_engines_strips_names(self) -> None:
        m = SpsaMetaData.model_validate({"engines": [" e1 ", "e2", ""]})
        assert m.engines == ["e1", "e2"]

    def test_engines_non_list(self) -> None:
        m = SpsaMetaData.model_validate({"engines": "not_a_list"})
        assert m.engines == []

    # -- engine_instances coercion --

    def test_instances_empty_key_skipped(self) -> None:
        m = SpsaMetaData.model_validate({"engine_instances": {"": "inst", "e1": "inst_a"}})
        assert m.engine_instances == {"e1": "inst_a"}

    # -- engine_stats coercion --

    def test_stats_non_dict_value_skipped(self) -> None:
        m = SpsaMetaData.model_validate({"engine_stats": {"e1": "not_dict", "e2": {"wins": 5}}})
        assert "e1" not in m.engine_stats
        assert m.engine_stats["e2"].wins == 5

    # -- engines_meta coercion --

    def test_engines_meta_non_dict_entries_skipped(self) -> None:
        m = SpsaMetaData.model_validate({"engines_meta": [{"name": "e1"}, "bad", 42]})
        assert len(m.engines_meta) == 1

    # -- ltc_regression coercion --

    def test_ltc_regression_non_dict_to_none(self) -> None:
        m = SpsaMetaData.model_validate({"ltc_regression": "invalid"})
        assert m.ltc_regression is None

    def test_ltc_regression_parsed(self) -> None:
        m = SpsaMetaData.model_validate({"ltc_regression": {"enabled": True, "pairs": 50}})
        assert m.ltc_regression is not None
        assert m.ltc_regression.is_enabled is True
        assert m.ltc_regression.pairs == 50

    # -- properties / methods --

    def test_effective_num_updates_prefers_num_updates(self) -> None:
        m = SpsaMetaData.model_validate({"num_updates": 100, "total": 200})
        assert m.effective_num_updates == 100

    def test_effective_num_updates_falls_back_to_total(self) -> None:
        m = SpsaMetaData.model_validate({"total": 200})
        assert m.effective_num_updates == 200

    def test_effective_num_updates_none(self) -> None:
        m = SpsaMetaData.model_validate({})
        assert m.effective_num_updates is None

    def test_resolve_spsa_config_returns_dict(self) -> None:
        m = SpsaMetaData.model_validate({"mobility": 0.5, "scale": 1.0, "A": 50.0})
        config = m.resolve_spsa_config()
        assert config is not None
        assert config["mobility"] == 0.5
        assert config["A"] == 50.0

    def test_resolve_spsa_config_none_when_empty(self) -> None:
        m = SpsaMetaData.model_validate({})
        assert m.resolve_spsa_config() is None

    def test_resolve_spsa_config_includes_ltc(self) -> None:
        m = SpsaMetaData.model_validate({"mobility": 0.5, "ltc_regression": {"enabled": True, "pairs": 50}})
        config = m.resolve_spsa_config()
        assert config is not None
        assert "ltc_regression" in config
        assert config["ltc_regression"]["enabled"] is True


# ---------------------------------------------------------------------------
# RawIndexUpdateEntry
# ---------------------------------------------------------------------------


class TestRawIndexUpdateEntry:
    def test_normal(self) -> None:
        entry = RawIndexUpdateEntry.model_validate(
            {
                "update_idx": 10,
                "timestamp": 1700000000,
                "params": {"a": 1.5, "b": 2.0},
                "s_plus": 0.55,
                "s_minus": 0.45,
                "step": 0.01,
                "gradients": {"a": 0.1},
                "deltas": {"a": 0.05},
                "perturbations": {"plus": {"a": 1.55}, "minus": {"a": 1.45}},
            }
        )
        assert entry.update_idx == 10
        assert entry.params == {"a": 1.5, "b": 2.0}
        assert entry.perturbations == {"plus": {"a": 1.55}, "minus": {"a": 1.45}}

    def test_defaults(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 0})
        assert entry.timestamp is None
        assert entry.params == {}
        assert entry.gradients == {}
        assert entry.perturbations == {}

    def test_float_dict_coercion(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 0, "params": {"a": "1.5", "b": "bad", "c": 3}})
        assert entry.params == {"a": 1.5, "c": 3.0}

    def test_float_dict_drops_inf(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 0, "gradients": {"a": float("inf"), "b": 1.0}})
        assert entry.gradients == {"b": 1.0}

    def test_perturbations_coercion(self) -> None:
        entry = RawIndexUpdateEntry.model_validate(
            {
                "update_idx": 0,
                "perturbations": {"plus": {"a": "1.5"}, "bad_key": "not_dict"},
            }
        )
        assert entry.perturbations == {"plus": {"a": 1.5}}

    def test_perturbations_non_dict(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 0, "perturbations": 42})
        assert entry.perturbations == {}

    def test_to_update_entry_dict(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 5, "params": {"a": 1.0}, "s_plus": 0.6})
        d = entry.to_update_entry_dict()
        assert isinstance(d, dict)
        assert d["update_idx"] == 5
        assert d["params"] == {"a": 1.0}
        assert d["s_plus"] == 0.6

    def test_extra_ignored(self) -> None:
        entry = RawIndexUpdateEntry.model_validate({"update_idx": 0, "unknown_extra": "value"})
        assert entry.update_idx == 0

    def test_update_idx_required(self) -> None:
        with pytest.raises(ValidationError):
            RawIndexUpdateEntry.model_validate({})


# ---------------------------------------------------------------------------
# IndexMetadata
# ---------------------------------------------------------------------------


class TestIndexMetadata:
    def test_normal(self) -> None:
        meta = IndexMetadata.model_validate(
            {
                "last_update_idx": 99,
                "total_updates": 100,
                "last_updated": 1700000000,
                "int_rounding_policy": "nearest",
                "crn_used": True,
                "update_mode": "async",
            }
        )
        assert meta.last_update_idx == 99
        assert meta.total_updates == 100
        assert meta.is_crn_used is True

    def test_defaults(self) -> None:
        meta = IndexMetadata.model_validate({})
        assert meta.last_update_idx is None
        assert meta.ltc_regression is None

    def test_ltc_regression_non_dict_to_none(self) -> None:
        meta = IndexMetadata.model_validate({"ltc_regression": "bad"})
        assert meta.ltc_regression is None

    def test_ltc_regression_parsed(self) -> None:
        meta = IndexMetadata.model_validate({"ltc_regression": {"status": "running", "last_update_idx": 50}})
        assert meta.ltc_regression is not None
        assert meta.ltc_regression.status == "running"


# ---------------------------------------------------------------------------
# IndexData
# ---------------------------------------------------------------------------


class TestIndexData:
    def test_normal(self) -> None:
        data = IndexData.model_validate(
            {
                "updates": [
                    {"update_idx": 0, "params": {"a": 1.0}},
                    {"update_idx": 1, "params": {"a": 1.1}},
                ],
                "metadata": {"last_update_idx": 1, "total_updates": 2},
            }
        )
        assert len(data.updates) == 2
        assert data.updates[0].update_idx == 0
        assert data.updates[1].params == {"a": 1.1}
        assert data.metadata.total_updates == 2

    def test_defaults(self) -> None:
        data = IndexData.model_validate({})
        assert data.updates == []
        assert data.metadata.last_update_idx is None

    def test_extra_ignored(self) -> None:
        data = IndexData.model_validate({"extra_key": True})
        assert data.updates == []


# ---------------------------------------------------------------------------
# SummaryCachePayload
# ---------------------------------------------------------------------------


class TestSummaryCachePayload:
    def test_normal(self) -> None:
        payload = SummaryCachePayload.model_validate(
            {
                "version": 1,
                "session_uuid": "abc-123",
                "events_offset": 100,
                "events_size": 50,
                "events_mtime_ns": 9999999,
                "aggregates": {"total_games": 200},
            }
        )
        assert payload.version == 1
        assert payload.session_uuid == "abc-123"
        assert payload.events_offset == 100
        assert payload.aggregates == {"total_games": 200}

    def test_defaults(self) -> None:
        payload = SummaryCachePayload.model_validate({})
        assert payload.version == 1
        assert payload.session_uuid is None
        assert payload.events_offset == 0
        assert payload.events_size == 0
        assert payload.events_mtime_ns == 0
        assert payload.aggregates == {}

    def test_extra_ignored(self) -> None:
        payload = SummaryCachePayload.model_validate({"unknown": 42})
        assert payload.version == 1
