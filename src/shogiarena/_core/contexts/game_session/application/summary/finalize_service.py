"""Finalize orchestration for tournament summary artifacts and logging."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.contexts.game_session.application.summary.artifact_service import (
    TournamentSummaryArtifactService,
)
from shogiarena._core.contexts.game_session.application.summary.final_payload_service import (
    TournamentSummaryFinalPayloadService,
)
from shogiarena._core.contexts.game_session.application.summary.reporting_service import (
    TournamentSummaryReportingService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.domain.run_health import (
    RunHealthInputs,
    RunTerminationReason,
    build_completion_status_payload,
    resolve_termination_reason,
)
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.runtime_watchdog import WatchdogSummaryPort
from shogiarena._core.shared.kernel.service_ports import SprtServicePort
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin

logger = logging.getLogger(__name__)

_COMPLETION_STATUS_FILENAME = "completion_status.json"
_COMPLETED_FLAG_FILENAME = "completed.flag"


def build_watchdog_payload(watchdog: WatchdogSummaryPort | None) -> JsonObject | None:
    """watchdog の計測値を artifact 形式へ写す。未計測は ``None``（キーは常に置く）。"""

    if watchdog is None:
        return None
    summary = watchdog.summary()
    return {
        "loop_lag_events": summary.loop_lag_events,
        "thread_lag_events": summary.thread_lag_events,
        # 進行中の停滞も反映した最大値（review finding M5）。
        "max_loop_lag_ms": round(summary.max_loop_lag_ms, 1),
        "max_thread_lag_ms": round(summary.max_thread_lag_ms, 1),
        "loop_threshold_ms": summary.loop_threshold_ms,
        "thread_threshold_ms": summary.thread_threshold_ms,
        # ring から押し出した event の件数と、run 全体の coverage 完全性（review finding M4）。
        "dropped_loop_events": summary.dropped_loop_events,
        "is_coverage_complete": summary.is_coverage_complete,
    }


def _has_valid_sprt_decision(sprt_service: SprtServicePort | None) -> bool:
    """SPRT が有効な decision へ到達しているか。

    stop reason 文字列だけを成功証拠にせず、``is_finished()`` と decision の存在を同時に確認する。
    """
    if sprt_service is None:
        return False
    try:
        if not sprt_service.is_finished():
            return False
        decision = getattr(sprt_service.get_status(), "decision", None)
    except (AttributeError, RuntimeError, ValueError) as exc:
        logger.warning("Failed to read SPRT decision while finalizing: %s", exc)
        return False
    if decision is None:
        return False
    return str(getattr(decision, "value", decision)) != "continue"


def _count_timeouts_by_origin(games: list[GameRecordPlayers]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for game in games:
        origin = game.get("timeout_origin")
        if origin:
            counts[origin] = counts.get(origin, 0) + 1
    return counts


def build_run_health_inputs(
    results: TournamentResults,
    games: list[GameRecordPlayers],
    *,
    stop_reason: str | None,
    sprt_service: SprtServicePort | None,
    forced_reason: RunTerminationReason | None = None,
) -> RunHealthInputs:
    """terminal status の入力を組み立てる（IO なし）。

    ``forced_reason`` は finalize 自体が失敗した場合など、stop reason より優先する理由を渡す。
    """
    error_games = sum(1 for game in games if game.get("result") == GameResult.ERROR)
    timeouts_by_origin = _count_timeouts_by_origin(games)
    not_played = max(0, results.total_games - results.completed_games_count - results.cancelled_games_count)
    reason = forced_reason or resolve_termination_reason(
        stop_reason=stop_reason,
        is_schedule_complete=not_played == 0,
        has_valid_sprt_decision=_has_valid_sprt_decision(sprt_service),
    )
    return RunHealthInputs(
        termination_reason=reason,
        scheduled=results.total_games,
        completed=results.completed_games_count,
        cancelled=results.cancelled_games_count,
        error_games=error_games,
        timeouts_by_origin=timeouts_by_origin,
        coverage_incomplete_timeouts=timeouts_by_origin.get(TimeoutOrigin.UNKNOWN.value, 0),
        stop_reason=stop_reason,
    )


@dataclass(frozen=True, slots=True)
class PendingRunHealth:
    """cleanup 後に commit する terminal status の入力。"""

    run_dir: Path
    inputs: RunHealthInputs


class TournamentSummaryFinalizeService:
    """Finalize tournament summary state, artifacts, and reporting output.

    順序は decisions.md Decision 7 に固定する。とくに ``completion_status.json`` と
    ``completed.flag`` は、失敗しうる finalize 処理と service cleanup がすべて終わった
    **後** にのみ書く。BTD 推定や cleanup が失敗した run に clean marker を残さない。
    """

    def __init__(
        self,
        *,
        payload_service: TournamentSummaryFinalPayloadService,
        artifact_service: TournamentSummaryArtifactService,
        reporting_service: TournamentSummaryReportingService,
    ) -> None:
        self._final_payload_service = payload_service
        self._artifact_service = artifact_service
        self._reporting_service = reporting_service

    async def finalize(self, runtime: TournamentSummaryRuntimeContext, *, results: TournamentResults) -> None:
        logger.debug("Finalizing tournament")
        run_dir = runtime.request.run_dir

        try:
            pending = await self._finalize_before_terminal_commit(runtime, results=results)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # cancellation は finalize の失敗ではない（review M4）。ここで
            # `finalization-error` を確定させると、中断経路が書こうとする `cancelled` が
            # 既存 status 保護に阻まれ、利用者の停止が故障として記録されてしまう。
            # cleanup だけ行い、terminal status は中断経路（2 段 commit）に任せる。
            await self._stop_services_quietly(runtime)
            raise
        except BaseException:
            # cleanup は例外経路でも必ず走らせる。ただし cleanup の失敗で元例外を隠さない。
            await self._stop_services_quietly(runtime)
            self._write_failure_status(run_dir, RunTerminationReason.FINALIZATION_ERROR, results=results)
            raise

        try:
            await runtime.actions.stop_services()
        except (asyncio.CancelledError, KeyboardInterrupt):
            # cleanup 中の中断も cleanup の失敗ではない（review M3）。ここで `cleanup-error` を
            # 確定させると、中断経路が書こうとする `cancelled` が既存 status 保護に阻まれる。
            raise
        except BaseException:
            self._write_failure_status(run_dir, RunTerminationReason.CLEANUP_ERROR, results=results)
            raise

        self._commit_terminal_status(runtime, pending)

    async def _finalize_before_terminal_commit(
        self,
        runtime: TournamentSummaryRuntimeContext,
        *,
        results: TournamentResults,
    ) -> PendingRunHealth:
        """失敗しうる finalize 処理をすべて行い、terminal status の入力を返す。"""

        try:
            await runtime.actions.flush_openbench()
        except RuntimeError as exc:
            if runtime.dependencies.is_openbench_strict_mode:
                raise
            logger.warning("OpenBench final flush failed; continuing (strict=false): %s", exc)

        runtime.actions.save_run_state(True)

        if runtime.request.is_dashboard_enabled:
            await runtime.actions.update_dashboard()

        results_data = self._final_payload_service.build_results_payload(results).to_json_object()
        self._artifact_service.write_tournament_results(runtime.request.run_dir, results_data)
        self._reporting_service.log_tournament_results(results)

        db_service = runtime.dependencies.db_service
        game_type = "generate" if runtime.actions.is_generate_run() else "arena"
        games = list(db_service.get_games_with_players(game_type=game_type)) if db_service else []

        engine_names = [str(e.name) for e in runtime.request.config.engines]
        anchor_name = engine_names[0] if engine_names else None
        btd = self._reporting_service.estimate_btd(games=games, anchor_name=anchor_name, engine_names=engine_names)
        self._reporting_service.log_btd_estimates(results=results, btd=btd, engine_names=engine_names)

        summary = self._final_payload_service.build_final_btd_payload(results=results, btd=btd).to_json_object()
        self._artifact_service.write_summary_btd(runtime.request.run_dir, summary, log_context="final")

        stop_controller = runtime.dependencies.stop_controller
        inputs = build_run_health_inputs(
            results,
            games,
            stop_reason=stop_controller.reason if stop_controller is not None else None,
            sprt_service=runtime.dependencies.sprt_service,
        )
        return PendingRunHealth(run_dir=runtime.request.run_dir, inputs=inputs)

    def write_interrupted_status(
        self,
        run_dir: Path,
        reason: RunTerminationReason,
        *,
        results: TournamentResults,
        watchdog: JsonObject | None = None,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        """finalize へ到達できない終了（cancellation / runtime error）の terminal status を書く。

        完全な集計は取れないため、判明している件数だけを載せた最小 artifact にする。
        ``completed.flag`` は作らないので、この artifact だけで完了扱いにはならない。

        2 段 commit（decisions.md Decision 7）:
        cancel 済み task は次の ``await`` で再び ``CancelledError`` を投げるため、cleanup を
        待ってからでは artifact を書けない経路が実在する。そこで cleanup 前に
        ``is_provisional=True`` で一度書き、cleanup 後に同じ内容を確定へ昇格させる。
        昇格できなかった場合は暫定 status がそのまま残るので、artifact は必ず存在する。

        finalize が既に terminal status を確定している場合は上書きしない。
        `finalization-error` / `cleanup-error` の方が具体的な理由を持つため。
        resume は dispatch 前に marker を無効化するので、ここで見えるのは今回の run の書き込みだけ。
        """
        existing = self._read_committed_status(run_dir)
        if existing is not None and not existing.get("is_provisional", False):
            logger.debug("Terminal run status already committed; keeping it instead of %s", reason.value)
            return
        self._write_failure_status(
            run_dir,
            reason,
            results=results,
            watchdog=watchdog,
            is_provisional=is_provisional,
            cleanup_error=cleanup_error,
        )

    @staticmethod
    def _read_committed_status(run_dir: Path) -> JsonObject | None:
        """既に置かれている terminal status を読む。読めない場合は「無い」として扱う。"""
        path = run_dir / _COMPLETION_STATUS_FILENAME
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None

    async def _stop_services_quietly(self, runtime: TournamentSummaryRuntimeContext) -> None:
        """primary exception を保ったまま cleanup を試みる。"""
        try:
            await runtime.actions.stop_services()
        except BaseException as exc:  # noqa: BLE001 - 元例外を優先するため握って記録する
            logger.error("Service cleanup failed while handling a finalization error: %s", exc, exc_info=True)

    def _commit_terminal_status(
        self,
        runtime: TournamentSummaryRuntimeContext,
        pending: PendingRunHealth,
    ) -> None:
        """terminal status を atomic write し、その後で compatibility marker を作る。"""

        payload = build_completion_status_payload(
            pending.inputs,
            watchdog=build_watchdog_payload(runtime.dependencies.watchdog),
        )
        write_json_atomic(pending.run_dir / _COMPLETION_STATUS_FILENAME, payload)
        self._touch_completed_flag(pending.run_dir)

    @staticmethod
    def _touch_completed_flag(run_dir: Path) -> None:
        """``completed.flag`` は status commit 済みを示す派生 marker（単独では成功証拠にしない）。"""
        try:
            (run_dir / _COMPLETED_FLAG_FILENAME).touch()
        except OSError as exc:
            # 既に atomic commit した status を正本として維持し、失敗へ書き換えない。
            logger.warning("Failed to create the completed.flag compatibility marker: %s", exc)

    @staticmethod
    def _write_failure_status(
        run_dir: Path,
        reason: RunTerminationReason,
        *,
        results: TournamentResults,
        watchdog: JsonObject | None = None,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        """最小の failed artifact を best effort で書く。元例外は上書きしない。

        ``completed.flag`` は作らないので、この artifact だけでは clean completion に見えない。
        """
        payload = build_completion_status_payload(
            RunHealthInputs(
                termination_reason=reason,
                scheduled=results.total_games,
                completed=results.completed_games_count,
                cancelled=results.cancelled_games_count,
                error_games=0,
            ),
            watchdog=watchdog,
            is_provisional=is_provisional,
            cleanup_error=cleanup_error,
        )
        try:
            write_json_atomic(run_dir / _COMPLETION_STATUS_FILENAME, payload)
        except OSError as exc:
            logger.error("Failed to write the failure completion status artifact: %s", exc)
        try:
            # 直前の実行が残した marker があれば消す。failed status と clean marker を共存させない。
            (run_dir / _COMPLETED_FLAG_FILENAME).unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Failed to remove a stale completed.flag after a finalization failure: %s", exc)


__all__ = ["PendingRunHealth", "TournamentSummaryFinalizeService", "build_run_health_inputs"]
