"""State and counter models for SPSA event update aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.wdl_counts import WdlCounts

_WdlCounts = WdlCounts


def _build_empty_wdl_counts() -> _WdlCounts:
    return _WdlCounts(wins=0, losses=0, draws=0)


def _build_empty_phase_wdl_by_side() -> dict[str, _WdlCounts]:
    return {"plus": _build_empty_wdl_counts(), "minus": _build_empty_wdl_counts()}


@dataclass(slots=True)
class _UpdateState:
    update_idx: int
    timestamp: int
    start_time: int | None = None
    end_time: int | None = None
    s_plus: float | None = None
    s_minus: float | None = None
    step: float | None = None
    delta_norm: float | None = None
    params: JsonObject = field(default_factory=dict)
    gradients: JsonObject = field(default_factory=dict)
    deltas: JsonObject = field(default_factory=dict)
    perturbations: JsonObject = field(default_factory=lambda: {"plus": {}, "minus": {}})
    is_pending: bool = True
    c_k: float | None = None
    a_k: float | None = None
    wins: int = 0
    losses: int = 0
    draws: int = 0
    phase_wdl: dict[str, _WdlCounts] = field(default_factory=_build_empty_phase_wdl_by_side)
    has_ltc_regression: bool = False
    ltc_regression: JsonObject | None = None
    has_ltc_rejected: bool = False
    is_ltc_rejected: bool = False
    ltc_reverted_to: int | None = None
    games_meta: dict[str, JsonObject] = field(default_factory=dict)
    games_order: list[str] = field(default_factory=list)
