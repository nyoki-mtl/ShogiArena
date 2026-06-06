"""Domain models shared across SPSA runtime components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict

from shogiarena._core.shared.kernel.json_types import JsonObject

PhaseLiteral: TypeAlias = Literal["plus", "minus", "ltc"]


@dataclass(slots=True)
class ParamEntry:
    """Single SPSA runtime parameter definition.

    ``name`` is the ShogiArena parameter id. ``option_name`` is the USI option
    sent to the engine; when omitted, ``name`` is used for older in-memory
    callers. ``step`` stores ``c_end`` and ``delta`` stores ``r_end`` for the
    classic SPSA schedule.
    """

    name: str
    type: str
    value: float
    min: float
    max: float
    step: float
    delta: float
    comment: str
    is_not_used: bool
    option_name: str | None = None
    value_encoding: Literal["integer", "decimal", "scaled_integer"] = "decimal"
    scale: float | None = None
    significant_digits: int = 9

    @property
    def engine_option_name(self) -> str:
        """Return the USI option name used for this parameter."""
        return self.option_name or self.name


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
    pairs_per_update: int
    inflight_factor: int


__all__ = ["ParamEntry", "PhaseLiteral", "SpsaAlgorithmConfig", "SpsaGamePayload"]
