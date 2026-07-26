"""Concrete grouped summary runtime context assembled by tournament runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shogiarena._core.contexts.game_session.ports.summary_runtime import (
    SummaryGameSpecPort,
    TournamentSummaryApiServerPort,
    TournamentSummaryConfigPort,
    TournamentSummaryRecordWriterPort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.runtime_watchdog import WatchdogSummaryPort
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort, SprtServicePort
from shogiarena._core.shared.kernel.session_hooks import SessionStopController

if TYPE_CHECKING:
    from shogiarena._core.contexts.game_session.application.summary.runtime_context_service import (
        SummaryRuntimeApiServerSourcePort,
        SummaryRuntimeOpenBenchClientPort,
        SummaryRuntimeRecordWriterSourcePort,
    )


@dataclass(slots=True)
class SummaryRuntimeBuildRequest:
    """Immutable request values used to assemble summary runtime context."""

    run_dir: Path
    config: TournamentSummaryConfigPort
    engine_metadata: list[JsonObject]
    engine_time_controls: tuple[dict[str, str], str | None]
    summary_source: str
    is_dashboard_enabled: bool


@dataclass(slots=True)
class SummaryRuntimeStateRefs:
    """Mutable runner-owned state referenced by summary services."""

    cancelled_game_ids: set[str]
    game_schedule: Sequence[SummaryGameSpecPort]
    completed_game_ids: set[str]
    original_total_games: int


@dataclass(slots=True)
class SummaryRuntimeDependencies:
    """External dependencies adapted for summary runtime use."""

    db_service: DatabaseServicePort | None
    api_server: TournamentSummaryApiServerPort | SummaryRuntimeApiServerSourcePort | None
    record_writer: TournamentSummaryRecordWriterPort | SummaryRuntimeRecordWriterSourcePort | None
    sprt_service: SprtServicePort | None
    is_openbench_strict_mode: bool = False
    openbench_client: SummaryRuntimeOpenBenchClientPort | None = None
    # run-health artifact 用の loop/thread stall 計測（task 0049）。未計測の run では None。
    watchdog: WatchdogSummaryPort | None = None
    # termination reason の正本（task 0052）。停止理由を count 算術から推定しないために渡す。
    stop_controller: SessionStopController | None = None


@dataclass(slots=True)
class SummaryRuntimeActionRefs:
    """Pure callbacks owned by runner orchestration."""

    engine_instance_defaults: Callable[[], dict[str, str | None]]
    resolve_tournament_type: Callable[[], str]
    build_rules_payload: Callable[[], JsonObject]
    build_sprt_payload: Callable[[], JsonObject]
    is_generate_run: Callable[[], bool]
    get_schedule_snapshot: Callable[[], Awaitable[JsonObject]]
    flush_openbench: Callable[[], Awaitable[None]]
    save_run_state: Callable[[bool], None]
    update_dashboard: Callable[[], Awaitable[None]]
    # terminal status を commit する前に全 service を停止する（task 0052、idempotent）。
    stop_services: Callable[[], Awaitable[None]]


@dataclass(slots=True)
class TournamentSummaryRuntimeContext:
    """Concrete grouped summary runtime context assembled by tournament runner."""

    request: SummaryRuntimeBuildRequest
    state: SummaryRuntimeStateRefs
    dependencies: SummaryRuntimeDependencies
    actions: SummaryRuntimeActionRefs


__all__ = [
    "SummaryRuntimeActionRefs",
    "SummaryRuntimeBuildRequest",
    "SummaryRuntimeDependencies",
    "SummaryRuntimeStateRefs",
    "TournamentSummaryRuntimeContext",
]
