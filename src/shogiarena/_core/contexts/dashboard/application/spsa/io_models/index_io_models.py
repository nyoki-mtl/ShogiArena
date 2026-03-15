"""Pydantic I/O models for SPSA index.json payloads."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.contexts.dashboard.application.spsa.io_models._scalar_maps import (
    ScalarMap,
    ScalarOrMapMap,
    coerce_scalar_map,
    coerce_scalar_or_map_map,
)
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none as _as_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_float_dict,
    coerce_nested_float_dict,
)


class LtcRegressionIndexInfo(BaseModel):
    """``ltc_regression`` section in index metadata."""

    status: str | None = None
    last_update_idx: int | None = None
    winrate: float | None = None
    elo: float | None = None
    pairs_played: int | None = None
    tuned_wins: int | None = None
    baseline_wins: int | None = None
    draws: int | None = None
    total_games: int | None = None
    timestamp: int | None = None
    baseline_update_idx: int | None = None
    baseline_variant_token: str | None = None
    tuned_variant_token: str | None = None
    is_accepted: bool | None = None
    sprt: ScalarMap | None = None
    sprt_decision: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("sprt", mode="before")
    @classmethod
    def _coerce_sprt(cls, v: JsonValue | None) -> ScalarMap | None:
        return coerce_scalar_map(v)


class RawIndexUpdateEntry(BaseModel):
    """Raw update item inside ``index.json``."""

    update_idx: int
    timestamp: int | None = None
    params: dict[str, float] = Field(default_factory=dict)
    s_plus: float | None = None
    s_minus: float | None = None
    step: float | None = None
    gradients: dict[str, float] = Field(default_factory=dict)
    deltas: dict[str, float] = Field(default_factory=dict)
    delta_norm: float | None = None
    batch_size: int | None = None
    total_games: int | None = None
    a_k: float | None = None
    c_k: float | None = None
    iteration_k: int | None = None
    perturbations: dict[str, dict[str, float]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")

    @field_validator("params", "gradients", "deltas", mode="before")
    @classmethod
    def _coerce_float_dict(cls, v: JsonValue | None) -> dict[str, float]:
        return coerce_float_dict(v)

    @field_validator("perturbations", mode="before")
    @classmethod
    def _coerce_perturbations(cls, v: JsonValue | None) -> dict[str, dict[str, float]]:
        return coerce_nested_float_dict(v)

    def to_update_entry_dict(self) -> JsonObject:
        return _as_json_object(self.model_dump()) or {}


class IndexMetadata(BaseModel):
    """``metadata`` section of ``index.json``."""

    last_update_idx: int | None = None
    total_updates: int | None = None
    last_updated: int | None = None
    int_rounding_policy: str | None = None
    is_crn_used: bool | None = Field(default=None, alias="crn_used")
    update_mode: str | None = None
    ltc_regression: LtcRegressionIndexInfo | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("ltc_regression", mode="before")
    @classmethod
    def _coerce_ltc(cls, v: JsonValue | None) -> ScalarOrMapMap | None:
        return coerce_scalar_or_map_map(v)


class IndexData(BaseModel):
    """Top-level ``index.json`` structure."""

    updates: list[RawIndexUpdateEntry] = Field(default_factory=list)
    metadata: IndexMetadata = Field(default_factory=IndexMetadata)

    model_config = ConfigDict(extra="ignore")


__all__ = ["IndexData", "IndexMetadata", "LtcRegressionIndexInfo", "RawIndexUpdateEntry"]
