"""Session execution scaffolding for session runners."""

from __future__ import annotations

import logging
from typing import TypeVar

from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.contexts.game_session.ports.run_runtime import (
    SessionExecutionRuntimePort,
    SessionRunResultBuilderPort,
    SessionRunResultPort,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import ProgressReporterPort
from shogiarena._core.shared.kernel.session_hooks import SessionStopController

TSessionContext = TypeVar("TSessionContext")
TSessionResults = TypeVar("TSessionResults")
TSprtStatus = TypeVar("TSprtStatus")
TRunResult = TypeVar("TRunResult", bound=SessionRunResultPort)

logger = logging.getLogger(__name__)


class TournamentSessionExecutionService:
    """Prepare and finalize session-level runner execution flow."""

    def prepare_progress(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        progress_reporter: ProgressReporterPort | None,
    ) -> ProgressReporterPort:
        previous = runner.progress
        if progress_reporter is not None:
            runner.set_progress(progress_reporter)
        return previous

    def restore_progress(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        previous: ProgressReporterPort,
    ) -> None:
        runner.set_progress(previous)

    async def prepare_session(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
    ) -> TSessionContext | None:
        await runner.prepare_run_dir()
        await runner.prepare_domain()
        await runner.init_services()

        dash = runner.get_dashboard_params()
        if dash is not None:
            run_dir, host, port, num_workers = dash
            await runner.start_dashboard_server(run_dir, host, port, num_workers)
            await runner.seed_initial_summary()

        session_context = runner.build_session_context()
        runner.set_session_context(session_context)
        return session_context

    async def handle_cancelled_run(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        controller: SessionStopController,
    ) -> None:
        controller.request_stop(reason="cancelled")
        runner.session_phase = "stopping"
        try:
            await self._interrupt_with_terminal_commit(runner, RunTerminationReason.CANCELLED)
        finally:
            runner.session_phase = "finished"
            runner.progress.finalize({"status": "cancelled"})

    async def handle_failed_run(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
    ) -> None:
        """cancellation 以外の異常終了で service を止め、terminal status を残す。

        finalize 自身の失敗は finalize 側が `finalization-error` / `cleanup-error` を
        既に書いているので、ここでの best-effort write では上書きしない。
        """
        try:
            await self._interrupt_with_terminal_commit(runner, RunTerminationReason.RUNTIME_ERROR)
        finally:
            runner.session_phase = "finished"

    async def _interrupt_with_terminal_commit(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        reason: RunTerminationReason,
    ) -> None:
        """中断経路の terminal status を 2 段で確定する（decisions.md Decision 7）。

        1. cleanup 前に暫定 status を書く。cancel 済み task は次の ``await`` で再び
           ``CancelledError`` を投げるため、cleanup を待ってからでは書けない経路がある。
           集計と watchdog もこの時点で確定させる（cleanup 後は読めない）。
        2. cleanup を行う。**失敗しても送出しない**。ここは既に別の例外を処理している
           最中なので、cleanup の例外を投げると元の中断理由を置き換えてしまう。
        3. cleanup の結果を反映して確定 status へ昇格する。昇格できなければ暫定が残る。
        """
        self._write_interrupted_run_health(runner, reason, is_provisional=True)
        cleanup_error = await self._stop_services_quietly(runner)
        self._write_interrupted_run_health(runner, reason, cleanup_error=cleanup_error)

    @staticmethod
    async def _stop_services_quietly(
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
    ) -> str | None:
        """cleanup を試み、失敗しても送出せずに要約だけ返す（元例外を保つ）。"""
        try:
            await runner.stop_services()
        except BaseException as exc:  # noqa: BLE001 - 元の中断理由を cleanup 失敗で置き換えない
            logger.error("Service cleanup failed while handling an interrupted run: %s", exc, exc_info=True)
            return f"{type(exc).__name__}: {exc}"
        return None

    @staticmethod
    def _write_interrupted_run_health(
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        reason: RunTerminationReason,
        *,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        try:
            runner.write_interrupted_run_health(reason, is_provisional=is_provisional, cleanup_error=cleanup_error)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            # best effort。元の中断理由を artifact write の失敗で上書きしない。
            logger.error("Failed to write the interrupted run-health artifact (%s): %s", reason.value, exc)

    async def finalize_session(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        result_builder: SessionRunResultBuilderPort[TSessionResults, TSprtStatus, TRunResult],
    ) -> TRunResult | None:
        if runner.are_services_closed():
            # Ctrl+C handled inside run_orchestrator closes services and swallows the cancellation,
            # so the run service's cancellation handler never sees it. This is the branch that a
            # real interrupted run takes, so the terminal status has to be written here too
            # (task 0052). Skip result computation: the services are already gone.
            self._write_interrupted_run_health(runner, RunTerminationReason.CANCELLED)
            runner.session_phase = "finished"
            runner.progress.finalize({"status": "cancelled"})
            return None

        runner.session_phase = "stopping"
        # finalize は自身の内部で service cleanup と terminal commit の順序を保証する
        # （decisions.md Decision 7）。ここでの cleanup は calculate_results や finalize の
        # 到達前に失敗した場合でも service を残さないための保険（Decision 9）。
        #
        # ``finally`` で無条件に停止すると、finalize 失敗時に cleanup が **2 度目** に呼ばれ、
        # そこで失敗すると元の finalization failure を置き換えてしまう（review H2）。
        # そのため例外中は握って記録するだけにし、正常路では既に閉じているかを確認してから呼ぶ。
        try:
            final = await runner.calculate_results()
            await runner.finalize_tournament(final)
        except BaseException:
            await self._stop_services_quietly(runner)
            raise
        if not runner.are_services_closed():
            # finalize が service を閉じずに戻る実装（SPSA 経路など）のための保険。
            # 正常路なので、ここでの cleanup 失敗は握らずに送出する。
            await runner.stop_services()
        runner.session_phase = "finished"
        sprt_status = runner.get_sprt_status()
        result = result_builder.build_tournament_run_result(final, sprt_status)
        runner.progress.finalize({"status": "finished", "run_id": result.run_id})
        return result


__all__ = ["TournamentSessionExecutionService"]
