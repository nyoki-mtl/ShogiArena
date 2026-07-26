"""Game-completion post-processing helpers for session runners."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import rsshogi.record

from shogiarena._core.contexts.game_session.application.sprt_service import SPRT_MODEL_GSPRT_PENTANOMIAL
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
from shogiarena._core.shared.kernel.sprt_ingestion import normalize_sprt_observation
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import should_sample_for_sprt
from shogiarena._core.shared.kernel.timeout_attribution import is_invalid_timeout_origin_value
from shogiarena._core.shared.kernel.timeout_breaker import TIMEOUT_BREAKER_POLICIES, resolve_breaker_stop_reason

logger = logging.getLogger(__name__)


def _read_timeout_origin(record: rsshogi.record.Record) -> str | None:
    """record metadata attributes から ``timeout_origin`` を安全に読む（task 0047）。"""
    metadata = getattr(record, "metadata", None)
    attributes = getattr(metadata, "attributes", None)
    if not isinstance(attributes, Mapping):
        return None
    value = attributes.get("timeout_origin")
    return value if isinstance(value, str) else None


class TournamentSessionCompletionService:
    """Completion-side metadata/result helpers extracted from runner."""

    def enrich_record_and_summarize(
        self,
        context: CompletionRuntimeContext,
        game_spec: CompletionGameSpecPort,
        *,
        record: rsshogi.record.Record,
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
        """terminal record は stop 要求の有無にかかわらず保存する（task 0052 / Decision 8）。

        ``PAUSED`` だけが non-terminal。stop で中断された局は runner 側が ``PAUSED`` を返すため、
        ここで ``ERROR`` を落とす必要はない。落とすと ``game.db`` と completed 集合が食い違い、
        stop 中に到着した ERROR の証拠が失われる（review finding M3）。
        """
        del is_stop_requested
        return result != GameResult.PAUSED

    def persist_record_outputs(
        self,
        context: CompletionPersistenceContext,
        *,
        record: rsshogi.record.Record,
        game_id: str,
        game_type: str,
        extract_participation: Callable[[object], tuple[object, ...]],
    ) -> None:
        db = context.db_service
        if db is not None:
            db.append_record_list([record])
            participation = extract_participation(record)
            if participation:
                game_id_raw = record.metadata.attributes.get("game_name")
                game_id_name = str(game_id_raw).strip() if game_id_raw is not None else ""
                db_game_id = db.get_game_id_by_name(game_id_name) if game_id_name else None
                if db_game_id is not None:
                    db.record_game_participation(game_id=db_game_id, participation=participation)

        if context.record_writer is not None:
            context.record_writer.append_record(record, game_id=game_id, game_type=game_type)

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
            game_id=str(game_spec.game_id),
        )

    def validate_completion_policies(
        self,
        context: CompletionStateContext,
        game_spec: CompletionGameSpecPort,
        *,
        result: GameResult,
        invalid_timeout_origin: str | None,
    ) -> None:
        """state を変更する前に result / SPRT sample policy を検証する（純粋、違反時は raise）。

        ここで fail-fast させることで、completed 集合や rating を書き換えた後に例外が飛んで
        部分 commit が残る事故を防ぐ（task 0052 / Decision 8）。
        """
        if context.sprt_service is None or context.sprt_pair is None:
            return
        tested_engine, _ = context.sprt_pair
        if tested_engine not in (str(game_spec.black_engine), str(game_spec.white_engine)):
            return
        if result == GameResult.ERROR and is_invalid_timeout_origin_value(invalid_timeout_origin):
            # 無効化された timeout は標本から除外するだけで、run を止めるほどの異常ではない。
            return
        should_sample_for_sprt(result, context=f"game {game_spec.game_id}")

    def commit_completion_state(
        self,
        context: CompletionStateContext,
        game_spec: CompletionGameSpecPort,
        *,
        summary: CompletionGameSummary,
        result: GameResult,
        invalid_timeout_origin: str | None = None,
    ) -> bool:
        game_id = str(game_spec.game_id)
        # SPRT を先に更新してから completed 集合へ入れる（task 0052 / Decision 8 の手順 4→5）。
        # policy 検証は process_game_completion で済ませてあるので、ここで raise しうる分岐はない。
        self.update_sprt_state(context, game_spec, result=result, invalid_timeout_origin=invalid_timeout_origin)
        context.completed_game_ids.add(game_id)
        context.completed_game_summaries[game_id] = summary
        self._apply_timeout_burst_breaker(context, result=result, invalid_timeout_origin=invalid_timeout_origin)
        if context.sprt_service and context.sprt_service.is_finished():
            if context.sprt_service.games_played >= context.sprt_min_games:
                # 停止を決めたこの時点で標本を締める（task 0052 / review H1）。
                # ラッチしないと、既に実行中だった局が後から到着して decision を
                # continue へ戻し、`sprt-finished` で止めた run が `incomplete` になる。
                context.sprt_service.latch_decision()
                context.stop_controller.request_stop(reason="sprt-finished")
        context.save_run_state()
        return bool(context.is_dashboard_enabled)

    def _apply_timeout_burst_breaker(
        self,
        context: CompletionStateContext,
        *,
        result: GameResult,
        invalid_timeout_origin: str | None,
    ) -> None:
        """無効 timeout が閾値に達したら run を停止する（task 0047、0052 で origin 別に分離）。

        入力は「結果がたまたま ``ERROR`` か」ではなく分類済みの timeout policy とする。
        origin ごとに独立した counter を持ち、有効な結果でその counter を戻す。
        閾値未満なら run は継続し、無効局は標本から除外されるだけになる。
        """
        origin = invalid_timeout_origin if result == GameResult.ERROR else None
        policy = TIMEOUT_BREAKER_POLICIES.get(origin or "") if origin is not None else None
        if policy is None:
            # 有効な結果、または breaker 対象外の origin。全 counter を戻す。
            context.consecutive_invalid_timeouts_by_origin.clear()
            return

        assert origin is not None  # policy が引けた時点で origin は非 None
        consecutive = context.consecutive_invalid_timeouts_by_origin.get(origin, 0) + 1
        context.consecutive_invalid_timeouts_by_origin.clear()
        context.consecutive_invalid_timeouts_by_origin[origin] = consecutive
        total = context.invalid_timeouts_by_origin.get(origin, 0) + 1
        context.invalid_timeouts_by_origin[origin] = total

        completed = len(context.completed_game_ids)
        # 閾値判定は resume の再評価と同じ純関数を使う（review H1）。
        reason = resolve_breaker_stop_reason(
            totals=context.invalid_timeouts_by_origin,
            consecutive=context.consecutive_invalid_timeouts_by_origin,
            completed=completed,
        )
        if reason is not None:
            logger.error(
                "Timeout breaker tripped: origin=%s consecutive=%d total=%d completed=%d; stopping dispatch",
                origin,
                consecutive,
                total,
                completed,
            )
            context.stop_controller.request_stop(reason=reason)

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
        record: rsshogi.record.Record,
        is_stop_requested: bool,
        extract_participation: Callable[[object], tuple[object, ...]],
        openbench_error_type: type[Exception],
    ) -> tuple[bool, GameResult]:
        game_id = str(game_spec.game_id)
        if game_id in context.state.completed_game_ids:
            # Idempotency guard: a duplicate completion for an already-recorded game must not
            # re-persist the record, re-update ratings, or re-count SPRT. Resumed games are
            # filtered out before dispatch, so this only catches duplicate completion events.
            logger.debug("Skipping already-completed game %s (duplicate completion)", game_id)
            return False, record.result
        result = record.result
        if result == GameResult.PAUSED:
            logger.debug("Skipping paused game %s; it remains eligible for resume", game_id)
            return False, result
        # Ordering is fixed by task 0052 / Decision 8: enrich and read the timeout decision, run the
        # pure policy pre-checks, persist the terminal record, then mutate rating/SPRT/completed
        # state. Nothing that can raise runs after the completed set has been updated.
        summary = self.enrich_record_and_summarize(context, game_spec, record=record)
        # Orchestrator-stall timeouts are recorded as ERROR with a ``timeout_origin`` attribute; read
        # it once here and let the SPRT path exclude such invalid games without aborting (task 0047).
        invalid_timeout_origin = _read_timeout_origin(record)
        self.validate_completion_policies(
            context.state,
            game_spec,
            result=result,
            invalid_timeout_origin=invalid_timeout_origin,
        )
        should_persist = self.should_persist_record(result, is_stop_requested=is_stop_requested)
        if should_persist:
            self.persist_record_outputs(
                context.persistence,
                record=record,
                game_id=str(game_spec.game_id),
                game_type="generate" if context.metadata.is_generate_run else "arena",
                extract_participation=extract_participation,
            )
        self.update_rating_state(context.rating, game_spec, result=result)
        update_dashboard = self.commit_completion_state(
            context.state,
            game_spec,
            summary=summary,
            result=result,
            invalid_timeout_origin=invalid_timeout_origin,
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
        attributes["run_mode"] = context.summary_source
        experiment = str(context.experiment_name or "").strip()
        if experiment:
            attributes["experiment_name"] = experiment
        if context.record_format is not None:
            attributes["record_format"] = context.record_format
        return attributes

    def summarize_game_completion(self, record: rsshogi.record.Record) -> CompletionGameSummary:
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
        invalid_timeout_origin: str | None = None,
    ) -> None:
        if context.sprt_service is None or context.sprt_pair is None:
            return

        a, _ = context.sprt_pair
        black = str(game_spec.black_engine)
        white = str(game_spec.white_engine)

        if a == black:
            is_tested_black = True
        elif a == white:
            is_tested_black = False
        else:
            return

        # Invalidated timeouts (orchestrator stall / unknown) are recorded as ERROR with an origin;
        # exclude them from the SPRT sample without aborting the run (task 0047). An un-classified
        # ERROR (a genuine crash) still fails fast via should_sample_for_sprt below.
        if result == GameResult.ERROR and is_invalid_timeout_origin_value(invalid_timeout_origin):
            logger.warning(
                "Excluding invalidated timeout game %s from SPRT sample (origin=%s)",
                game_spec.game_id,
                invalid_timeout_origin,
            )
            return
        # Centralized policy: PAUSED is excluded from the sample, ERROR/INVALID fail-fast.
        if not should_sample_for_sprt(result, context=f"game {game_spec.game_id}"):
            logger.debug("Skipping SPRT update for paused game %s", game_spec.game_id)
            return

        # 正規化は resume の replay と共有する（review H2）。分岐が 2 箇所にあると、
        # resume した run だけ別の統計の下で継続しうる。
        observation = normalize_sprt_observation(result, is_tested_black=is_tested_black)

        if context.sprt_service.model == SPRT_MODEL_GSPRT_PENTANOMIAL:
            # Buffer the game by (opening sfen, pair slot); the pair completes when the reversed
            # colour arrives. round_num // 2 collapses the two colour-reversed rounds into one slot.
            context.sprt_service.add_game_observation(
                sfen=str(game_spec.initial_sfen),
                pair_slot=game_spec.round_num // 2,
                is_tested_black=is_tested_black,
                tested_score=observation.tested_score,
            )
            return

        context.sprt_service.add_game_result(observation.trinomial_result)


__all__ = ["TournamentSessionCompletionService"]
