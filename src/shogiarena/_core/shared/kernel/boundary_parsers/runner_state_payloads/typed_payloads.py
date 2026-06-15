from __future__ import annotations

from typing import TypeAlias

from typing_extensions import TypedDict

from shogiarena._core.shared.kernel.json_types import JsonValue

# Wire type for assignment override (generic, not context-specific).
# Canonical typed form is AssignmentOverride in tournament context.
_AssignmentOverrideWire: TypeAlias = str | dict[str, object] | None


class _CancelledGameEntryType(TypedDict, total=False):
    game_id: str
    black: str | None
    white: str | None
    round: int | None
    sfen: str | None
    assignment: _AssignmentOverrideWire
    should_require_install: bool


class _SprtPendingHalf(TypedDict):
    sfen: str
    pair_slot: int
    is_tested_black: bool
    score: float


class _SprtStateSnapshot(TypedDict):
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
    pending: list[_SprtPendingHalf]


class _TournamentRunStateType(TypedDict, total=False):
    schedule_hash: str | None
    total_games: int | None
    cancelled_game_ids: list[str]
    completed_games_count: int | None
    cancelled_games_count: int | None
    cancelled_games: list[_CancelledGameEntryType]
    original_total_games: int | None
    game_display_order: dict[str, int]
    is_finished: bool
    created_at: str | None
    updated_at: str | None
    sprt_state: _SprtStateSnapshot | None
    openbench_state: dict[str, JsonValue] | None
    game_instance_overrides: dict[str, _AssignmentOverrideWire]


__all__ = [
    "_AssignmentOverrideWire",
    "_TournamentRunStateType",
]
