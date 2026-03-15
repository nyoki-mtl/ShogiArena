"""Game-completion post-processing helpers for session runners."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import rshogi.record

from shogiarena._core.contexts.game_session.ports.completion_runtime import (
    CompletionGameSpecPort,
    CompletionGameSummary,
    CompletionMetadataContext,
    CompletionOpenBenchContext,
    CompletionPersistenceContext,
    CompletionRatingContext,
    CompletionRuntimeContext,
    CompletionStateContext,
)
from shogiarena._core.shared.kernel.game_results import GameResult, game_result_name
from shogiarena._core.shared.kernel.scalar_coercion.api import datetime_to_iso

logger = logging.getLogger(__name__)


class TournamentSessionCompletionService:
    """Completion-side metadata/result helpers extracted from runner."""

    def enrich_record_and_summarize(
        self,
        context: CompletionRuntimeContext,
        game_spec: CompletionGameSpecPort,
        *,
        record: rshogi.record.GameRecord,
    ) -> CompletionGameSummary:
        metadata_attrs = dict(record.metadata.attributes)
        record.update_metadata(
            {
                "black_player": game_spec.black_engine,
                "white_player": game_spec.white_engine,
                "game_type": ("generate" if context.metadata.is_generate_run else "arena"),
            }
        )
        for key, value in self.build_metadata_attributes(context.metadata, metadata_attrs).items():
            record.set_metadata_attribute(str(key), str(value))
        return self.summarize_game_completion(record)

    @staticmethod
    def should_persist_record(result: GameResult, *, is_stop_requested: bool) -> bool:
        should_persist = result != GameResult.PAUSED
        if should_persist and is_stop_requested and result in (GameResult.PAUSED, GameResult.ERROR):
            should_persist = False
        return should_persist

    def persist_record_outputs(
        self,
        context: CompletionPersistenceContext,
        *,
        record: rshogi.record.GameRecord,
        extract_participation: Callable[[object], tuple[object, ...]],
    ) -> None:
        db = context.db_service
        if db is not None:
            db.append_record_list([record])
            participation = extract_participation(record)
            if participation:
                game_id_raw = record.metadata.attributes.get("game_name")
                game_id_name = str(game_id_raw).strip() if game_id_raw is not None else ""
                game_id = db.get_game_id_by_name(game_id_name) if game_id_name else None
                if game_id is not None:
                    db.record_game_participation(game_id=game_id, participation=participation)

        if context.record_writer is not None:
            context.record_writer.append_record(record)

    def update_rating_state(
        self,
        context: CompletionRatingContext,
        game_spec: CompletionGameSpecPort,
        *,
        result: GameResult,
    ) -> None:
        rating_service = context.rating_service
        if rating_service is None:
            return
        rating_service.update_ratings(
            game_spec.black_engine,
            game_spec.white_engine,
            result,
        )

    def commit_completion_state(
        self,
        context: CompletionStateContext,
        game_spec: CompletionGameSpecPort,
        *,
        summary: CompletionGameSummary,
        result: GameResult,
    ) -> bool:
        game_id = str(game_spec.game_id)
        context.completed_game_ids.add(game_id)
        context.completed_game_summaries[game_id] = summary
        self.update_sprt_state(context, game_spec, result=result)
        if context.sprt_service and context.sprt_service.is_finished():
            if context.sprt_service.games_played >= context.sprt_min_games:
                context.stop_controller.request_stop(reason="sprt-finished")
        context.save_run_state()
        return bool(context.is_dashboard_enabled)

    async def sync_openbench(
        self,
        context: CompletionOpenBenchContext,
        *,
        openbench_error_type: type[Exception],
    ) -> None:
        try:
            await context.sync_after_game()
        except openbench_error_type as exc:
            if context.is_strict_mode:
                raise
            logger.warning(
                "OpenBench submission failed; continuing (strict=false): %s",
                exc,
            )

    async def process_game_completion(
        self,
        context: CompletionRuntimeContext,
        game_spec: CompletionGameSpecPort,
        *,
        record: rshogi.record.GameRecord,
        is_stop_requested: bool,
        extract_participation: Callable[[object], tuple[object, ...]],
        openbench_error_type: type[Exception],
    ) -> tuple[bool, GameResult]:
        summary = self.enrich_record_and_summarize(context, game_spec, record=record)
        result = record.result
        should_persist = self.should_persist_record(result, is_stop_requested=is_stop_requested)
        if should_persist:
            self.persist_record_outputs(context.persistence, record=record, extract_participation=extract_participation)
        self.update_rating_state(context.rating, game_spec, result=result)
        update_dashboard = self.commit_completion_state(
            context.state,
            game_spec,
            summary=summary,
            result=result,
        )
        await self.sync_openbench(context.openbench, openbench_error_type=openbench_error_type)
        return update_dashboard, result

    @staticmethod
    def build_progress_payload(
        context: CompletionStateContext,
        game_spec: CompletionGameSpecPort,
        *,
        result: GameResult,
    ) -> dict[str, object]:
        return {
            "game_id": str(game_spec.game_id),
            "black": str(game_spec.black_engine),
            "white": str(game_spec.white_engine),
            "game_result": game_result_name(result),
            "completed_games": len(context.completed_game_ids),
            "total_games": int(context.total_games),
        }

    def build_metadata_attributes(
        self,
        context: CompletionMetadataContext,
        existing: Mapping[str, str] | None,
    ) -> dict[str, str]:
        attributes: dict[str, str] = {}
        if existing is not None:
            for key, value in existing.items():
                if value is not None:
                    attributes[str(key)] = str(value)
        attributes["runMode"] = context.summary_source
        experiment = str(context.experiment_name or "").strip()
        if experiment:
            attributes["experimentName"] = experiment
        if context.record_format is not None:
            attributes["recordFormat"] = context.record_format
        return attributes

    def summarize_game_completion(self, record: rshogi.record.GameRecord) -> CompletionGameSummary:
        result_obj = record.result

        total_plies = len(record.moves)
        start_raw = record.metadata.start_date
        end_raw = record.metadata.end_date
        start_time = datetime_to_iso(start_raw)
        end_time = datetime_to_iso(end_raw)

        summary: CompletionGameSummary = {
            "game_result": game_result_name(result_obj),
            "total_plies": total_plies,
            "start_time": start_time,
            "end_time": end_time,
        }
        return summary

    def update_sprt_state(
        self,
        context: CompletionStateContext,
        game_spec: CompletionGameSpecPort,
        *,
        result: GameResult,
    ) -> None:
        if context.sprt_service is None or context.sprt_pair is None:
            return

        a, _ = context.sprt_pair
        black = str(game_spec.black_engine)
        white = str(game_spec.white_engine)

        if a == black:
            sprt_result = (
                GameResult.WHITE_WIN
                if result.is_black_win()
                else GameResult.BLACK_WIN
                if result.is_white_win()
                else GameResult.DRAW_BY_MAX_PLIES
            )
        elif a == white:
            sprt_result = (
                GameResult.WHITE_WIN
                if result.is_white_win()
                else GameResult.BLACK_WIN
                if result.is_black_win()
                else GameResult.DRAW_BY_MAX_PLIES
            )
        else:
            return

        context.sprt_service.add_game_result(sprt_result)


__all__ = ["TournamentSessionCompletionService"]
