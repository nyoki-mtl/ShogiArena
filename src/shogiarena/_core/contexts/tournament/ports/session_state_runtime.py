"""Runtime contracts for tournament run-state persistence/resume services."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, TypedDict

from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.service_ports import SprtServicePort


class TournamentRunOptionsPort(Protocol):
    """Runner options required by run-state store."""

    should_skip_resume: bool


class TournamentModelDumpPort(Protocol):
    """Minimal model-dump contract used for config payload persistence."""

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class TournamentEnginePort(Protocol):
    """Minimal engine contract required by schedule regeneration and config payload."""

    @property
    def name(self) -> str | None: ...


class TournamentInitialPositionsPort(Protocol):
    """Initial position contract required by schedule regeneration."""

    @property
    def flip_policy(self) -> str: ...

    def generate(self, count: int, seed: str) -> list[str]: ...


ScheduleSeedLike: TypeAlias = int | float | str | bytes | bytearray | None

ScheduleSeed: TypeAlias = str
"""内部正規型。boundary で ScheduleSeedLike から正規化する。"""


def normalize_schedule_seed(seed: ScheduleSeedLike) -> ScheduleSeed:
    """Boundary normalization: 広い union を内部正規型に変換する。"""
    if seed is None:
        return ""
    return str(seed)


class TournamentStateConfigPort(Protocol):
    """Minimal tournament config contract used by run-state services."""

    @property
    def experiment_name(self) -> str | None: ...

    @property
    def engines(self) -> Sequence[TournamentEnginePort]: ...

    @property
    def tournament(self) -> Any: ...

    @property
    def rules(self) -> Any: ...

    @property
    def sprt(self) -> TournamentModelDumpPort | None: ...

    @property
    def openbench(self) -> TournamentModelDumpPort | None: ...

    @property
    def records_output(self) -> TournamentModelDumpPort | None: ...

    def get_schedule_hash(self) -> str: ...


class TournamentScheduleGeneratorPort(Protocol):
    """Schedule generation contract used during setup/resume."""

    def generate_schedule(
        self,
        engines: list[TournamentEnginePort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: TournamentInitialPositionsPort,
    ) -> list[GameSpec]: ...


@dataclass(slots=True)
class TournamentScheduleGeneratorRuntimeContext(TournamentScheduleGeneratorPort):
    """Concrete schedule generator context for state store runtime."""

    generate_schedule_fn: Callable[
        [list[TournamentEnginePort], int, ScheduleSeed, TournamentInitialPositionsPort],
        list[GameSpec],
    ]

    def generate_schedule(
        self,
        engines: list[TournamentEnginePort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: TournamentInitialPositionsPort,
    ) -> list[GameSpec]:
        return self.generate_schedule_fn(engines, games_per_pair, seed, initial_positions)


class TournamentOpenBenchStatePort(Protocol):
    """OpenBench state persistence contract used by run-state services."""

    def snapshot_state(self) -> JsonObject | None: ...
    def restore_state(self, openbench_state: Mapping[str, JsonValue]) -> None: ...


class AssignmentOverride(TypedDict, total=False):
    """対局割当 override の正本型。"""

    shared: str | None
    black: str | None
    white: str | None
    should_require_install: bool
    mode: Literal["shared", "per_color"] | None


AssignmentOverridePayload: TypeAlias = str | AssignmentOverride | None


# ---------------------------------------------------------------------------
# Explicit mutable state port
# ---------------------------------------------------------------------------


class TournamentMutableStatePort(Protocol):
    """Explicit mutable state contract for tournament setup/save operations.

    Satisfied structurally by ``TournamentRunnerState``.
    Replaces the former getter/setter callback forest.
    """

    game_schedule: list[GameSpec]
    completed_game_ids: set[str]
    completed_game_summaries: dict[str, JsonObject]
    cancelled_game_ids: set[str]
    cancelled_specs: dict[str, GameSpec]
    game_display_order: dict[str, int]
    original_total_games: int
    sprt: SprtServicePort | None


# ---------------------------------------------------------------------------
# Save context (persistence payload)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TournamentStateSaveContext:
    """Context for tournament run-state persistence.

    Carries the mutable state handle, config, and focused payload-builder
    callbacks.  Replaces the former ``TournamentSessionStateSaveRuntimePort``
    and its callback-heavy concrete context.
    """

    run_dir: Path
    config: TournamentStateConfigPort
    state: TournamentMutableStatePort
    openbench: TournamentOpenBenchStatePort
    build_rules_payload: Callable[[], JsonObject]
    is_generate_run: Callable[[], bool]
    serialize_assignment_override: Callable[[GameSpec], JsonObject | None]
    shared_override_label: Callable[[GameSpec], str | None]


# ---------------------------------------------------------------------------
# Setup context (setup / resume flow)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TournamentStateSetupContext:
    """Context for tournament state setup/resume operations.

    Carries the mutable state handle, config, scheduler, and focused
    schedule-operation callbacks.  Replaces the former
    ``TournamentSessionStateSetupRuntimePort`` and its 25-field callback
    forest concrete context.
    """

    run_dir: Path
    run_options: TournamentRunOptionsPort
    config: TournamentStateConfigPort
    scheduler: TournamentScheduleGeneratorPort
    state: TournamentMutableStatePort
    openbench: TournamentOpenBenchStatePort
    reorder_and_shuffle: Callable[[list[GameSpec]], list[GameSpec]]
    reset_schedule_tracking: Callable[[], None]
    write_schedule_file: Callable[[list[GameSpec]], None]
    notify_schedule_available: Callable[[], None]
    reset_display_order: Callable[[], None]
    apply_assignment_override: Callable[[GameSpec, AssignmentOverridePayload], None]
    ensure_display_order_for_specs: Callable[[Iterable[GameSpec]], None]
    refresh_game_assignments: Callable[[], None]
    build_save_context: Callable[[], TournamentStateSaveContext]


__all__ = [
    "AssignmentOverride",
    "AssignmentOverridePayload",
    "ScheduleSeed",
    "TournamentEnginePort",
    "TournamentInitialPositionsPort",
    "TournamentScheduleGeneratorRuntimeContext",
    "TournamentStateSaveContext",
    "TournamentStateSetupContext",
    "normalize_schedule_seed",
]
