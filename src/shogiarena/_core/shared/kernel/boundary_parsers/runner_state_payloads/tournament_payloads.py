from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_game_result,
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


class _CompletedGameSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_result: str | None = None
    total_plies: int | None = None
    start_time: str | None = None
    end_time: str | None = None

    @field_validator("total_plies", mode="before")
    @classmethod
    def _coerce_optional_int(cls, value: Any | None) -> int | None:
        return coerce_optional_int_strict(value)

    @field_validator("game_result", mode="before")
    @classmethod
    def _coerce_game_result_name(cls, value: Any | None) -> str | None:
        if value is None:
            return None
        return coerce_game_result(value, is_strict=True).name


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


class _SprtStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elo0: float
    elo1: float
    alpha: float
    beta: float
    wins: int
    draws: int
    losses: int
    games_played: int
    llr: float


class _RunStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, object] | None = None
    schedule_hash: str | None = None
    total_games: int | None = None
    completed_game_ids: list[str] = Field(default_factory=list)
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
    completed_game_summaries: dict[str, _CompletedGameSummaryPayload] = Field(default_factory=dict)
    game_instance_overrides: dict[str, _AssignmentOverrideWire] = Field(default_factory=dict)

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

    @field_validator("completed_game_summaries", mode="before")
    @classmethod
    def _coerce_completed_game_summaries(cls, value: Any | None) -> dict[str, _CompletedGameSummaryPayload]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("completed_game_summaries must be a mapping")
        summaries: dict[str, _CompletedGameSummaryPayload] = {}
        for key, raw_summary in value.items():
            key_str = coerce_optional_text(key)
            if key_str is None:
                raise ValueError("game_id keys cannot be empty")
            if not isinstance(raw_summary, Mapping):
                raise ValueError("completed_game_summaries values must be mappings")
            payload = _CompletedGameSummaryPayload.model_validate(raw_summary)
            summaries[key_str] = payload
        return summaries

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


__all__ = ["_CompletedGameSummaryPayload", "_RunStatePayload"]
