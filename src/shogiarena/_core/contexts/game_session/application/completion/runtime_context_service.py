"""Completion runtime-context assembly service."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, MutableSet

from shogiarena._core.contexts.game_session.ports.completion_runtime import (
    CompletionGameSummary,
    CompletionMetadataContext,
    CompletionOpenBenchContext,
    CompletionPersistenceContext,
    CompletionRatingContext,
    CompletionRecordWriterPort,
    CompletionRuntimeContext,
    CompletionStateContext,
    CompletionStopControllerPort,
)
from shogiarena._core.shared.kernel.service_ports import (
    DatabaseServicePort,
    RatingServicePort,
    SprtServicePort,
)


class CompletionRuntimeContextService:
    """Build completion runtime DTOs from runner-owned state and services."""

    @staticmethod
    def build_metadata_context(
        *,
        is_generate_run: bool,
        summary_source: str,
        experiment_name: str | None,
        record_format: str | None,
    ) -> CompletionMetadataContext:
        return CompletionMetadataContext(
            is_generate_run=is_generate_run,
            summary_source=summary_source,
            experiment_name=experiment_name,
            record_format=record_format,
        )

    @staticmethod
    def build_persistence_context(
        *,
        db_service: DatabaseServicePort | None,
        record_writer: CompletionRecordWriterPort | None,
    ) -> CompletionPersistenceContext:
        return CompletionPersistenceContext(
            db_service=db_service,
            record_writer=record_writer,
        )

    @staticmethod
    def build_rating_context(
        *,
        rating_service: RatingServicePort | None,
    ) -> CompletionRatingContext:
        return CompletionRatingContext(rating_service=rating_service)

    @staticmethod
    def build_state_context(
        *,
        completed_game_ids: MutableSet[str],
        completed_game_summaries: MutableMapping[str, CompletionGameSummary],
        sprt_service: SprtServicePort | None,
        sprt_pair: tuple[str, str] | None,
        sprt_min_games: int,
        stop_controller: CompletionStopControllerPort,
        is_dashboard_enabled: bool,
        total_games: int,
        save_run_state: Callable[[], None],
    ) -> CompletionStateContext:
        return CompletionStateContext(
            completed_game_ids=completed_game_ids,
            completed_game_summaries=completed_game_summaries,
            sprt_service=sprt_service,
            sprt_pair=sprt_pair,
            sprt_min_games=sprt_min_games,
            stop_controller=stop_controller,
            is_dashboard_enabled=is_dashboard_enabled,
            total_games=total_games,
            save_run_state=save_run_state,
        )

    @staticmethod
    def build_runtime_context(
        *,
        metadata: CompletionMetadataContext,
        persistence: CompletionPersistenceContext,
        rating: CompletionRatingContext,
        state: CompletionStateContext,
        openbench: CompletionOpenBenchContext,
    ) -> CompletionRuntimeContext:
        return CompletionRuntimeContext(
            metadata=metadata,
            persistence=persistence,
            rating=rating,
            state=state,
            openbench=openbench,
        )


__all__ = ["CompletionRuntimeContextService"]
