"""Domain models shared across SPSA runtime components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject

PhaseLiteral: TypeAlias = Literal["plus", "minus", "ltc"]


@dataclass(slots=True)
class ParamEntry:
    """Single SPSA parameter definition parsed from disk."""

    name: str
    type: str
    value: float
    min: float
    max: float
    step: float
    delta: float
    comment: str
    is_not_used: bool


@dataclass(frozen=True)
class SpsaGamePayload:
    """Metadata passed to lifecycle hooks when an SPSA game completes."""

    update_idx: int
    tuned_params: list[ParamEntry]
    current_params: list[ParamEntry]
    is_tuned_as_black: bool
    winner_code: int
    phase: PhaseLiteral
    event_family: str = "spsa"


class SpsaAlgorithmConfig(TypedDict):
    """SPSA algorithm parameter snapshot for dashboard serialization."""

    num_updates: int
    mobility: float
    scale: float
    a0: float
    A: float | None
    alpha: float
    gamma: float
    is_crn_enabled: bool
    int_rounding: str
    int_ck_floor: float
    update_mode: str
    should_snap_float_to_step: bool
    early_stop: JsonObject | None
    update_batch_size: int | None
    inflight_factor: int


__all__ = ["ParamEntry", "PhaseLiteral", "SpsaAlgorithmConfig", "SpsaGamePayload"]
