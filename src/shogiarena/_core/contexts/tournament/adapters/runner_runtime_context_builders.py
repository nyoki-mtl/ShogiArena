"""Runtime context builders for tournament runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping, MutableSet
from typing import cast

from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.application.completion.openbench_context_service import (
    CompletionOpenBenchClientPort,
    CompletionOpenBenchContextService,
)
from shogiarena._core.contexts.game_session.application.completion.runtime_context_service import (
    CompletionRuntimeContextService,
)
from shogiarena._core.contexts.game_session.ports.completion_runtime import (
    CompletionGameSummary,
    CompletionRecordWriterPort,
    CompletionRuntimeContext,
    CompletionStopControllerPort,
)
from shogiarena._core.contexts.tournament.application.schedule_generation import (
    EngineSpecPort,
    InitialPositionSource,
)
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    ScheduleSeed,
    TournamentEnginePort,
    TournamentInitialPositionsPort,
)
from shogiarena._core.shared.kernel.initial_position_entry import InitialPositionEntry
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.service_ports import (
    DatabaseServicePort,
    RatingServicePort,
    SprtServicePort,
)


def _completion_summary_to_json(summary: CompletionGameSummary) -> JsonObject:
    return {
        "game_result": summary.get("game_result"),
        "total_plies": summary.get("total_plies"),
        "start_time": summary.get("start_time"),
        "end_time": summary.get("end_time"),
    }


def build_tournament_completion_runtime_context(
    *,
    completion_runtime_context_service: CompletionRuntimeContextService,
    completion_openbench_context_service: CompletionOpenBenchContextService,
    completed_game_summaries: MutableMapping[str, JsonObject],
    config: TournamentRunConfig,
    summary_source: str,
    is_generate_run: bool,
    db_service: DatabaseServicePort | None,
    record_writer: CompletionRecordWriterPort | None,
    rating_service: RatingServicePort | None,
    completed_game_ids: MutableSet[str],
    sprt_service: SprtServicePort | None,
    sprt_pair: tuple[str, str] | None,
    sprt_min_games: int,
    stop_controller: CompletionStopControllerPort,
    is_dashboard_enabled: bool,
    total_games: int,
    save_run_state_fn: Callable[[], None],
    consecutive_invalid_timeouts_by_origin: MutableMapping[str, int],
    invalid_timeouts_by_origin: MutableMapping[str, int],
    openbench_client: CompletionOpenBenchClientPort | None,
    sync_after_game_fn: Callable[[], Awaitable[None]],
) -> CompletionRuntimeContext:
    normalized_summaries: dict[str, CompletionGameSummary] = {}
    for game_id, raw_summary in completed_game_summaries.items():
        summary: CompletionGameSummary = {
            "game_result": coerce_str(raw_summary.get("game_result")),
            "total_plies": coerce_int(raw_summary.get("total_plies")),
            "start_time": coerce_str(raw_summary.get("start_time")),
            "end_time": coerce_str(raw_summary.get("end_time")),
        }
        normalized_summaries[game_id] = summary

    # Keep runtime updates wired to runner-owned state by normalizing in-place.
    completed_game_summaries.clear()
    for game_id, summary in normalized_summaries.items():
        completed_game_summaries[game_id] = _completion_summary_to_json(summary)
    shared_completed_summaries = cast(MutableMapping[str, CompletionGameSummary], completed_game_summaries)

    records_output = config.records_output
    record_format = records_output.format if records_output is not None else None

    metadata = completion_runtime_context_service.build_metadata_context(
        is_generate_run=is_generate_run,
        summary_source=summary_source,
        experiment_name=config.experiment_name,
        record_format=record_format,
    )
    persistence = completion_runtime_context_service.build_persistence_context(
        db_service=db_service,
        record_writer=record_writer,
    )
    rating = completion_runtime_context_service.build_rating_context(rating_service=rating_service)
    state = completion_runtime_context_service.build_state_context(
        completed_game_ids=completed_game_ids,
        completed_game_summaries=shared_completed_summaries,
        sprt_service=sprt_service,
        sprt_pair=sprt_pair,
        sprt_min_games=sprt_min_games,
        stop_controller=stop_controller,
        is_dashboard_enabled=is_dashboard_enabled,
        total_games=total_games,
        save_run_state=save_run_state_fn,
        consecutive_invalid_timeouts_by_origin=consecutive_invalid_timeouts_by_origin,
        invalid_timeouts_by_origin=invalid_timeouts_by_origin,
    )
    openbench = completion_openbench_context_service.build_context(
        client=openbench_client,
        sync_after_game=sync_after_game_fn,
    )
    return completion_runtime_context_service.build_runtime_context(
        metadata=metadata,
        persistence=persistence,
        rating=rating,
        state=state,
        openbench=openbench,
    )


class _StateStoreEngineSpec:
    name: str | None
    instance_id: str | None

    def __init__(self, name: str | None) -> None:
        self.name = name
        self.instance_id = None


class _StateStoreInitialPositionSource:
    flip_policy: str
    generate_fn: Callable[[int, str], list[str]]
    generate_entries_fn: Callable[[int, str], list[object]] | None

    def __init__(
        self,
        *,
        flip_policy: str,
        generate_fn: Callable[[int, str], list[str]],
        generate_entries_fn: Callable[[int, str], list[object]] | None = None,
    ) -> None:
        self.flip_policy = flip_policy
        self.generate_fn = generate_fn
        self.generate_entries_fn = generate_entries_fn

    def generate(self, count: int, seed: str) -> list[str]:
        return self.generate_fn(count, seed)

    def generate_entries(self, count: int, seed: str) -> list[InitialPositionEntry]:
        if self.generate_entries_fn is None:
            return [InitialPositionEntry(initial_sfen=sfen) for sfen in self.generate_fn(count, seed)]
        entries: list[InitialPositionEntry] = []
        for item in self.generate_entries_fn(count, seed):
            if isinstance(item, InitialPositionEntry):
                entries.append(item)
            else:
                entries.append(InitialPositionEntry(initial_sfen=str(item)))
        return entries


def generate_schedule_for_state_store(
    scheduler: object,
    engines: list[TournamentEnginePort],
    games_per_pair: int,
    seed: ScheduleSeed,
    initial_positions: TournamentInitialPositionsPort,
) -> list[GameSpec]:
    """Generate schedule using state-store compatible adapters."""
    scheduler_engines: list[EngineSpecPort] = [_StateStoreEngineSpec(name=engine.name) for engine in engines]
    generate_entries = getattr(initial_positions, "generate_entries", None)
    scheduler_initial_positions: InitialPositionSource = _StateStoreInitialPositionSource(
        flip_policy=initial_positions.flip_policy,
        generate_fn=initial_positions.generate,
        generate_entries_fn=generate_entries if callable(generate_entries) else None,
    )
    generate_schedule = getattr(scheduler, "generate_schedule", None)
    if not callable(generate_schedule):
        raise TypeError("scheduler must expose generate_schedule method")
    return generate_schedule(
        engines=scheduler_engines,
        games_per_pair=games_per_pair,
        seed=seed,
        initial_positions=scheduler_initial_positions,
    )


__all__ = [
    "build_tournament_completion_runtime_context",
    "generate_schedule_for_state_store",
]
