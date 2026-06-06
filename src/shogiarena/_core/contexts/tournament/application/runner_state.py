"""Typed mutable state for tournament runner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.service_ports import (
    DatabaseServicePort,
    RatingServicePort,
    SprtServicePort,
)


@dataclass
class TournamentRunnerState:
    """Typed container for tournament runner mutable state.

    Replaces the implicit ``Any`` attribute contracts previously spread
    across ``TournamentRunnerCompletionMixin`` and
    ``TournamentRunnerRuntimeMixin``.
    """

    # -- Schedule / game lifecycle -----------------------------------------
    game_schedule: list[GameSpec] = field(default_factory=list)
    completed_game_ids: set[str] = field(default_factory=set)
    completed_game_summaries: dict[str, JsonObject] = field(default_factory=dict)
    cancelled_game_ids: set[str] = field(default_factory=set)
    cancelled_specs: dict[str, GameSpec] = field(default_factory=dict)
    game_assignments: dict[str, dict[str, str | None]] = field(default_factory=dict)
    game_display_order: dict[str, int] = field(default_factory=dict)
    original_total_games: int = 0

    # -- Execution control -------------------------------------------------
    session_phase: str = "starting"
    should_stop_when_idle: bool = False
    schedule_wait_event: asyncio.Event = field(default_factory=asyncio.Event)
    pending_reschedule: list[GameSpec] | None = None
    pending_reschedule_seed: int | None = None
    reschedule_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    completion_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # -- Service references (initialized during runner lifecycle) ----------
    db_service: DatabaseServicePort | None = None
    rating_service: RatingServicePort | None = None
    sprt: SprtServicePort | None = None
    sprt_pair: tuple[str, str] | None = None
    sprt_min_games: int = 0

    # -- Engine metadata cache ---------------------------------------------
    engine_metadata_cache: list[JsonObject] | None = None
    engine_metadata_runtime_sig: str | None = None
    engine_time_controls_cache: tuple[dict[str, str], str | None] | None = None

    # -- Sealed run-artifact contract --------------------------------------
    sealed_schedule_hash: str | None = None
    sealed_resume_hash: str | None = None


__all__ = ["TournamentRunnerState"]
