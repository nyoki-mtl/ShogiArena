"""Explicit context for schedule service ↔ runner interaction.

Replaces the monolithic ``TournamentScheduleRunnerPort`` with a focused
context object that bundles only the infrastructure / config / callback
references the schedule helpers actually need.  Mutable schedule state
is passed separately as ``TournamentRunnerState``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from shogiarena._core.contexts.tournament.application.schedule_generation import (
    EngineSpecPort,
    GameScheduler,
    InitialPositionSource,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec

# ---------------------------------------------------------------------------
# Minimal structural Protocols for infrastructure refs
# ---------------------------------------------------------------------------


class _ScheduleConfigTournament(Protocol):
    """``config.tournament`` surface used by schedule helpers."""

    @property
    def seed(self) -> int: ...

    @seed.setter
    def seed(self, value: int) -> None: ...

    @property
    def games_per_pair(self) -> int: ...

    @property
    def game_order(self) -> str: ...


class _ScheduleConfigRules(Protocol):
    @property
    def initial_positions(self) -> InitialPositionSource: ...


class _ScheduleConfig(Protocol):
    """``config`` surface used by schedule helpers."""

    @property
    def tournament(self) -> _ScheduleConfigTournament: ...

    @property
    def engines(self) -> Sequence[EngineSpecPort]: ...

    @property
    def rules(self) -> _ScheduleConfigRules: ...


class _StopController(Protocol):
    @property
    def is_stop_requested(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    def request_stop(self, *, reason: str | None = None) -> None: ...


class _OrchestratorStop(Protocol):
    """Minimal orchestrator surface needed by schedule mutation."""

    def request_stop(self) -> None: ...


class _TournamentOrchestrator(Protocol):
    """Minimal surface for restored-game enqueueing."""

    async def enqueue_restored_game(self, spec: GameSpec, display_order: int) -> None: ...


class _ActiveGame(Protocol):
    @property
    def roles(self) -> Any: ...

    @property
    def started_ts_sec(self) -> float | None: ...


class _Instance(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def type(self) -> Any: ...

    @property
    def active_game_by_id(self) -> dict[str, _ActiveGame]: ...


class _InstancePool(Protocol):
    def list_instances(self) -> list[_Instance]: ...

    def get_instance(self, name: str) -> _Instance | None: ...

    def ensure_local_instance(self) -> _Instance: ...


# ---------------------------------------------------------------------------
# Schedule context
# ---------------------------------------------------------------------------


@dataclass
class TournamentScheduleContext:
    """Infrastructure, config and callback refs for schedule service.

    Passed alongside ``TournamentRunnerState`` to schedule service methods
    instead of the monolithic runner-wide Protocol.
    """

    # -- Config and scheduler -----------------------------------------------
    config: _ScheduleConfig
    scheduler: GameScheduler

    # -- Infrastructure refs ------------------------------------------------
    instance_pool: _InstancePool | None
    run_dir: Path
    stop_controller: _StopController
    is_dashboard_enabled: bool
    engine_instance_defaults: dict[str, str | None]

    # -- Mutable orchestrator refs (updated during runner lifecycle) ---------
    orchestrator: _OrchestratorStop | None = None
    tournament_orchestrator: _TournamentOrchestrator | None = None

    # -- Callbacks ----------------------------------------------------------
    save_run_state: Callable[..., None] = field(default=lambda: None)
    update_dashboard: Callable[[], Awaitable[None]] = field(default=lambda: asyncio.sleep(0))
    reorder_and_shuffle: Callable[[list[GameSpec]], list[GameSpec]] = field(default=lambda g: g)
    is_generate_run: Callable[[], bool] = field(default=lambda: False)


__all__ = [
    "TournamentScheduleContext",
]
