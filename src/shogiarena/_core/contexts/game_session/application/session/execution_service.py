"""Session execution scaffolding for session runners."""

from __future__ import annotations

from typing import TypeVar

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
        await runner.stop_services()
        runner.session_phase = "finished"
        runner.progress.finalize({"status": "cancelled"})

    async def finalize_session(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        result_builder: SessionRunResultBuilderPort[TSessionResults, TSprtStatus, TRunResult],
    ) -> TRunResult | None:
        if runner.are_services_closed():
            # Ctrl+C handled inside run_orchestrator closes services; skip result computation.
            runner.session_phase = "finished"
            runner.progress.finalize({"status": "cancelled"})
            return None

        runner.session_phase = "stopping"
        final = await runner.calculate_results()
        await runner.finalize_tournament(final)
        await runner.stop_services()
        runner.session_phase = "finished"
        sprt_status = runner.get_sprt_status()
        result = result_builder.build_tournament_run_result(final, sprt_status)
        runner.progress.finalize({"status": "finished", "run_id": result.run_id})
        return result


__all__ = ["TournamentSessionExecutionService"]
