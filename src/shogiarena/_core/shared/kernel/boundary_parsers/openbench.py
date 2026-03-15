"""Parser helpers for OpenBench client state boundary."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

_BOUNDARY_ID = "BND-OPENBENCH-STATE"


def _coerce_blacklist(value: JsonValue | None) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("blacklist must be a list")
    blacklisted: list[int] = []
    for raw in value:
        parsed = coerce_int(raw)
        if parsed is None or parsed < 0:
            raise ValueError("blacklist entries must be non-negative integers")
        blacklisted.append(parsed)
    return blacklisted


class _CountersStateModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    losses: int = Field(default=0, ge=0)
    draws: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)
    ll: int = Field(default=0, ge=0)
    ld: int = Field(default=0, ge=0)
    dd: int = Field(default=0, ge=0)
    dw: int = Field(default=0, ge=0)
    ww: int = Field(default=0, ge=0)
    crashes: int = Field(default=0, ge=0)
    timelosses: int = Field(default=0, ge=0)
    illegals: int = Field(default=0, ge=0)


class _ClientStateSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    submitted: _CountersStateModel = Field(default_factory=_CountersStateModel)
    last_synced_games: int = Field(default=0, ge=0)
    target_test_id: int | None = Field(default=None, gt=0)
    claimed_test_id: int | None = Field(default=None, gt=0)
    result_id: int | None = Field(default=None, gt=0)
    blacklist: list[int] = Field(default_factory=list)

    @field_validator("target_test_id", "claimed_test_id", "result_id", mode="before")
    @classmethod
    def _normalize_positive_ids(cls, value: JsonValue | None) -> int | None:
        if value is None:
            return None
        parsed = coerce_int(value)
        if parsed is None or parsed <= 0:
            return None
        return parsed

    @field_validator("blacklist", mode="before")
    @classmethod
    def _coerce_blacklist(cls, value: JsonValue | None) -> list[int]:
        return _coerce_blacklist(value)


def parse_openbench_client_state_boundary(payload: Mapping[str, object], *, path: str = "root") -> JsonObject:
    """Validate and parse OpenBench client snapshot payload."""
    parsed = parse_wire(
        boundary_id=_BOUNDARY_ID,
        payload=payload,
        model=_ClientStateSnapshotModel,
        path=path,
    )
    return parsed.model_dump(mode="python")


__all__ = [
    "parse_openbench_client_state_boundary",
]
