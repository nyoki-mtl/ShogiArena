from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_non_negative_int_strict,
    coerce_optional_int_strict,
    coerce_optional_non_negative_int_strict,
    coerce_optional_text,
)

from .typed_payloads import _AssignmentOverrideWire


def _coerce_assignment_override(value: Any | None) -> _AssignmentOverrideWire:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        parsed = _AssignmentOverridePayloadModel.model_validate(value)
        return parsed.model_dump(mode="python")
    raise ValueError("Assignment override must be string or mapping")


class _AssignmentOverridePayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shared: str | None = None
    black: str | None = None
    white: str | None = None
    should_require_install: bool = False
    mode: str | None = None

    @field_validator("shared", "black", "white", mode="before")
    @classmethod
    def _coerce_instance_id(cls, value: Any | None) -> str | None:
        return coerce_optional_text(value)

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, value: Any | None) -> str | None:
        normalized = coerce_optional_text(value)
        if normalized is None:
            return None
        normalized = normalized.lower()
        if normalized in {"shared", "per_color"}:
            return normalized
        raise ValueError("assignment mode must be 'shared' or 'per_color'")


class _CancelledGameEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str = ""
    black: str | None = None
    white: str | None = None
    round: int | None = None
    sfen: str | None = None
    assignment: _AssignmentOverrideWire = None
    should_require_install: bool = False

    @field_validator("round", mode="before")
    @classmethod
    def _coerce_round(cls, value: Any | None) -> int | None:
        return coerce_optional_int_strict(value)

    @field_validator("assignment", mode="before")
    @classmethod
    def _coerce_assignment(cls, value: Any | None) -> _AssignmentOverrideWire:
        return _coerce_assignment_override(value)


class _SprtPendingHalfPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sfen: str
    pair_slot: int
    is_tested_black: bool
    score: float


class _SprtStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    elo0: float
    elo1: float
    alpha: float
    beta: float
    wins: int
    draws: int
    losses: int
    games_played: int
    llr: float
    min_pairs: int
    penta_bins: list[int]
    pending: list[_SprtPendingHalfPayload]
    # 停止判定のラッチ（task 0052 / review H1）。1.1.0 より前の state.json には無い。
    is_decision_latched: bool = False
    late_games: int = 0


class _RunStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_hash: str | None = None
    resume_hash: str | None = None
    total_games: int | None = None
    cancelled_game_ids: list[str] = Field(default_factory=list)
    completed_games_count: int | None = None
    cancelled_games_count: int | None = None
    cancelled_games: list[_CancelledGameEntryPayload] = Field(default_factory=list)
    original_total_games: int | None = None
    game_display_order: dict[str, int] = Field(default_factory=dict)
    is_finished: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    sprt_state: _SprtStatePayload | None = None
    openbench_state: Mapping[str, object] | None = None
    game_instance_overrides: dict[str, _AssignmentOverrideWire] = Field(default_factory=dict)
    # timeout breaker の counter（task 0052 / review M3）。1.1.0 より前の state.json には無い。
    invalid_timeouts_by_origin: dict[str, int] = Field(default_factory=dict)
    consecutive_invalid_timeouts_by_origin: dict[str, int] = Field(default_factory=dict)

    @field_validator("game_display_order", mode="before")
    @classmethod
    def _coerce_display_order(cls, value: Any | None) -> dict[str, int]:
        if not isinstance(value, Mapping):
            raise ValueError("game_display_order must be a mapping")
        items: dict[str, int] = {}
        for key, raw_value in value.items():
            key_str = coerce_optional_text(key)
            if key_str is None:
                raise ValueError("game_id keys cannot be empty")
            items[key_str] = coerce_non_negative_int_strict(raw_value)
        return items

    @field_validator("game_instance_overrides", mode="before")
    @classmethod
    def _coerce_game_instance_overrides(cls, value: Any | None) -> dict[str, _AssignmentOverrideWire]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("game_instance_overrides must be a mapping")
        overrides: dict[str, _AssignmentOverrideWire] = {}
        for key, raw in value.items():
            key_str = coerce_optional_text(key)
            if key_str is None:
                raise ValueError("game_id keys cannot be empty")
            overrides[key_str] = _coerce_assignment_override(raw)
        return overrides

    @field_validator("original_total_games", "completed_games_count", "cancelled_games_count", mode="before")
    @classmethod
    def _coerce_optional_non_negative_int(cls, value: Any | None) -> int | None:
        return coerce_optional_non_negative_int_strict(value)


__all__ = ["_RunStatePayload"]
