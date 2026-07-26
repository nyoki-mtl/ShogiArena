"""Top-level run use-case orchestration for tournament sessions."""

from __future__ import annotations

import asyncio
from typing import TypeVar

from shogiarena._core.contexts.game_session.ports.run_runtime import (
    SessionExecutionServicePort,
    SessionRunLoopServicePort,
    SessionRunResultBuilderPort,
    SessionRunResultPort,
    SessionRunRuntimePort,
)
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import ProgressReporterPort

TSessionContext = TypeVar("TSessionContext")
TSessionResults = TypeVar("TSessionResults")
TSprtStatus = TypeVar("TSprtStatus")
TRunResult = TypeVar("TRunResult", bound=SessionRunResultPort)


class TournamentSessionRunService:
    """Execute the tournament runner flow via composed application services."""

    async def run(
        self,
        runner: SessionRunRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        execution_service: SessionExecutionServicePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        run_loop_service: SessionRunLoopServicePort[TSessionContext],
        result_builder: SessionRunResultBuilderPort[TSessionResults, TSprtStatus, TRunResult],
        progress_reporter: ProgressReporterPort | None,
    ) -> TRunResult | None:
        previous = execution_service.prepare_progress(
            runner,
            progress_reporter=progress_reporter,
        )
        controller = runner.stop_controller
        try:
            session_context = await execution_service.prepare_session(runner)
            await run_loop_service.run_loop(
                controller=controller,
                runner=runner,
                session_context=session_context,
            )
            return await execution_service.finalize_session(runner, result_builder=result_builder)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Honour Ctrl+C while paused by making sure services stop exactly once.
            # A cancelled run never reaches the finalizer, so the terminal status has to be
            # written here or the documented `status=failed / termination_reason=cancelled`
            # contract would never hold in practice (task 0052).
            await execution_service.handle_cancelled_run(runner, controller=controller)
            return None
        except BaseException:
            # prepare / run-loop / finalize のどこで落ちても service を残さない（Decision 9）。
            # finalize 内部の失敗は自分で artifact を書くので、ここでは runtime-error として扱う。
            await execution_service.handle_failed_run(runner)
            raise
        finally:
            execution_service.restore_progress(runner, previous=previous)


__all__ = ["TournamentSessionRunService"]
