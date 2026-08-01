"""Typed records shared across SPSA ledger ports and adapters。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from shogiarena._core.shared.kernel.json_types import JsonObject


class SpsaUpdateState(StrEnum):
    """Durable SPSA update stages。"""

    PLANNED = "PLANNED"
    GAMES_RUNNING = "GAMES_RUNNING"
    GAMES_COMPLETE = "GAMES_COMPLETE"
    CANDIDATE_COMPUTED = "CANDIDATE_COMPUTED"
    LTC_PENDING = "LTC_PENDING"
    LTC_RUNNING = "LTC_RUNNING"
    ACCEPTED = "ACCEPTED"
    REVERTED = "REVERTED"
    COMMITTED = "COMMITTED"


@dataclass(frozen=True)
class LedgerGameObservation:
    """Validated ledger observation row。"""

    run_id: str
    game_id: str
    update_idx: int
    pair_id: str
    attempt_id: str
    observation_kind: str
    result_kind: str
    game_db_id: int
    evidence_digest: str
    observed_at: str


@dataclass(frozen=True)
class LedgerPairAssignment:
    """One complete deterministic pair assignment prepared before dispatch."""

    pair_id: str
    opening: JsonObject
    color_assignment: JsonObject
    flips: Mapping[str, int]
    rounding_samples: JsonObject


__all__ = ["LedgerGameObservation", "LedgerPairAssignment", "SpsaUpdateState"]
