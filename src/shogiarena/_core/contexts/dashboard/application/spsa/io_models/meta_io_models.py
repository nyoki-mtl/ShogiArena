"""Pydantic I/O models for SPSA meta.json payloads."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shogiarena._core.contexts.dashboard.application.spsa.io_models._scalar_maps import ScalarMap, coerce_scalar_map
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none as _as_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float_dict, coerce_int, coerce_optional_text


class EngineStatEntry(BaseModel):
    """Engine stats entry in ``meta.json``."""

    wins: int = 0
    losses: int = 0
    draws: int = 0
    games: int = 0

    model_config = ConfigDict(extra="ignore")


class LtcRegressionMetaConfig(BaseModel):
    """``ltc_regression`` section in ``meta.json``."""

    is_enabled: bool = Field(default=False, alias="enabled")
    pairs: int | None = None
    sprt_elo0: float | None = None
    sprt_elo1: float | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("is_enabled", mode="before")
    @classmethod
    def _coerce_is_enabled(cls, v: JsonValue | None) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return v != 0
        return False


class SpsaMetaData(BaseModel):
    """Top-level ``meta.json`` I/O schema."""

    session_uuid: str | None = None
    num_updates: int | None = None
    total: int | None = None
    experiment_name: str | None = None
    initial_params: dict[str, float] = Field(default_factory=dict)
    parameters_path: str | None = None

    engine_time_controls: dict[str, str] = Field(default_factory=dict)
    default_time_control: str | None = None
    engines: list[str] = Field(default_factory=list)
    engine_instances: dict[str, str | None] = Field(default_factory=dict)
    engine_stats: dict[str, EngineStatEntry] = Field(default_factory=dict)
    engines_meta: list[ScalarMap] = Field(default_factory=list)

    mobility: float | None = None
    scale: float | None = None
    a0: float | None = None
    spsa_A: float | None = Field(None, alias="A")
    alpha: float | None = None
    gamma: float | None = None
    is_crn_enabled: bool | None = Field(default=None, alias="crn_enabled")
    int_rounding: str | None = None
    int_ck_floor: float | None = None
    update_mode: str | None = None
    should_snap_float_to_step: bool | None = Field(default=None, alias="snap_float_to_step")
    early_stop: ScalarMap | None = None
    update_batch_size: int | None = None
    inflight_factor: float | None = None

    ltc_regression: LtcRegressionMetaConfig | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_camel_case_keys(cls, data: JsonValue | None) -> dict[str, JsonValue]:
        normalized = _as_json_object(data)
        if normalized is None:
            return {}
        mapping = {
            "engineTimeControls": "engine_time_controls",
            "defaultTimeControl": "default_time_control",
            "engineInstances": "engine_instances",
            "engineStats": "engine_stats",
            "enginesMeta": "engines_meta",
        }
        for camel, snake in mapping.items():
            if camel in normalized and snake not in normalized:
                normalized[snake] = normalized[camel]
        return normalized

    @field_validator("session_uuid", mode="before")
    @classmethod
    def _strip_uuid(cls, v: JsonValue | None) -> str | None:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return None

    @field_validator("initial_params", mode="before")
    @classmethod
    def _coerce_initial_params(cls, v: JsonValue | None) -> dict[str, float]:
        return coerce_float_dict(v)

    @field_validator("engine_time_controls", mode="before")
    @classmethod
    def _coerce_time_controls(cls, v: JsonValue | None) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, str] = {}
        for name, spec in v.items():
            key = coerce_optional_text(name)
            if not key:
                continue
            result[key] = "-" if spec is None else (coerce_optional_text(spec) or "-")
        return result

    @field_validator("engines", mode="before")
    @classmethod
    def _coerce_engines(cls, v: JsonValue | None) -> list[str]:
        if not isinstance(v, list):
            return []
        result: list[str] = []
        for name in v:
            normalized = coerce_optional_text(name)
            if normalized:
                result.append(normalized)
        return result

    @field_validator("engine_instances", mode="before")
    @classmethod
    def _coerce_instances(cls, v: JsonValue | None) -> dict[str, str | None]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, str | None] = {}
        for name, inst in v.items():
            key = coerce_optional_text(name)
            if not key:
                continue
            result[key] = coerce_optional_text(inst)
        return result

    @field_validator("engine_stats", mode="before")
    @classmethod
    def _coerce_stats(cls, v: JsonValue | None) -> dict[str, dict[str, int]]:
        if not isinstance(v, dict):
            return {}
        result: dict[str, dict[str, int]] = {}
        for name, stat in v.items():
            key = coerce_optional_text(name)
            if not key or not isinstance(stat, dict):
                continue
            normalized: dict[str, int] = {}
            for inner_key, inner_val in stat.items():
                parsed = coerce_int(inner_val)
                if parsed is None:
                    continue
                normalized_key = coerce_optional_text(inner_key)
                if normalized_key is None:
                    continue
                normalized[normalized_key] = parsed
            result[key] = normalized
        return result

    @field_validator("engines_meta", mode="before")
    @classmethod
    def _coerce_engines_meta(cls, v: JsonValue | None) -> list[ScalarMap]:
        if not isinstance(v, list):
            return []
        normalized: list[ScalarMap] = []
        for entry in v:
            payload = coerce_scalar_map(entry)
            if payload is not None:
                normalized.append(payload)
        return normalized

    @field_validator("ltc_regression", mode="before")
    @classmethod
    def _coerce_ltc(cls, v: JsonValue | None) -> ScalarMap | None:
        return coerce_scalar_map(v)

    @property
    def effective_num_updates(self) -> int | None:
        return self.num_updates if self.num_updates is not None else self.total

    def resolve_spsa_config(self) -> JsonObject | None:
        keys = (
            "num_updates",
            "mobility",
            "scale",
            "a0",
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
        )
        config: JsonObject = {}
        data = _as_json_object(self.model_dump(by_alias=True)) or {}
        for key in keys:
            if key in data and data[key] is not None:
                config[key] = data[key]
        if self.spsa_A is not None:
            config["A"] = self.spsa_A
        if self.ltc_regression is not None:
            ltc_config = _as_json_object(self.ltc_regression.model_dump(exclude_none=True, by_alias=True))
            if ltc_config is not None:
                config["ltc_regression"] = ltc_config
        return config or None


__all__ = ["EngineStatEntry", "LtcRegressionMetaConfig", "SpsaMetaData"]
