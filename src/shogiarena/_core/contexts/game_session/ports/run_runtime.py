"""Runtime port contracts for session execution/run services."""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import (
    OrchestratorPort,
    ProgressReporterPort,
)
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks, SessionStopController

TSessionContext = TypeVar("TSessionContext")
TSessionResults = TypeVar("TSessionResults")
TSprtStatus = TypeVar("TSprtStatus")


class SessionRunResultPort(Protocol):
    """Minimal run-result contract required by execution service."""

    run_id: str


TRunResult = TypeVar("TRunResult", bound=SessionRunResultPort)


class SessionRunResultBuilderPort(Protocol[TSessionResults, TSprtStatus, TRunResult]):
    """Result builder contract consumed by execution/run services."""

    def build_tournament_run_result(
        self,
        results: TSessionResults,
        sprt_status: TSprtStatus | None,
    ) -> TRunResult: ...


class SessionExecutionRuntimePort(Protocol[TSessionContext, TSessionResults, TSprtStatus, TRunResult]):
    """Runner runtime contract consumed by session execution service."""

    session_phase: str

    @property
    def progress(self) -> ProgressReporterPort: ...

    def set_progress(self, reporter: ProgressReporterPort) -> None: ...

    def get_sprt_status(self) -> TSprtStatus | None: ...

    async def prepare_run_dir(self) -> None: ...

    async def prepare_domain(self) -> None: ...

    async def init_services(self) -> None: ...

    def get_dashboard_params(self) -> tuple[Path, str, int, int] | None: ...

    async def start_dashboard_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int: ...

    async def seed_initial_summary(self) -> None: ...

    def build_session_context(self) -> TSessionContext | None: ...

    def set_session_context(self, session_context: TSessionContext | None) -> None: ...

    async def stop_services(self) -> None: ...

    def are_services_closed(self) -> bool: ...

    async def calculate_results(self) -> TSessionResults: ...

    async def finalize_tournament(self, results: TSessionResults) -> None: ...


class SessionExecutionServicePort(Protocol[TSessionContext, TSessionResults, TSprtStatus, TRunResult]):
    """Execution-service contract consumed by run service."""

    def prepare_progress(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        progress_reporter: ProgressReporterPort | None,
    ) -> ProgressReporterPort: ...

    def restore_progress(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        previous: ProgressReporterPort,
    ) -> None: ...

    async def prepare_session(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
    ) -> TSessionContext | None: ...

    async def handle_cancelled_run(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        controller: SessionStopController,
    ) -> None: ...

    async def finalize_session(
        self,
        runner: SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
        *,
        result_builder: SessionRunResultBuilderPort[TSessionResults, TSprtStatus, TRunResult],
    ) -> TRunResult | None: ...


class SessionRunLoopRuntimePort(Protocol[TSessionContext]):
    """Runner runtime contract consumed by run-loop service."""

    session_phase: str
    pending_reschedule: object | None
    should_stop_when_idle: bool

    def reset_stop_controller(self, controller: SessionStopController) -> None: ...

    def has_pending_games(self) -> bool: ...

    def are_services_closed(self) -> bool: ...

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks: ...

    def set_lifecycle_hooks(self, hooks: GameLifecycleHooks) -> None: ...

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: TSessionContext | None,
    ) -> OrchestratorPort: ...

    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort) -> None: ...

    async def run_orchestrator(
        self,
        orchestrator: OrchestratorPort,
        run_coro: Awaitable[object],
    ) -> object | None: ...

    async def has_applied_pending_reschedule(self) -> bool: ...

    async def wait_for_new_schedule(self) -> None: ...


class SessionRunLoopServicePort(Protocol[TSessionContext]):
    """Run-loop service contract consumed by run service."""

    async def run_loop(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        *,
        controller: SessionStopController,
        session_context: TSessionContext | None,
    ) -> None: ...


@runtime_checkable
class SessionRunRuntimePort(
    SessionExecutionRuntimePort[TSessionContext, TSessionResults, TSprtStatus, TRunResult],
    SessionRunLoopRuntimePort[TSessionContext],
    Protocol,
):
    """Runner runtime contract consumed by run service."""

    @property
    def stop_controller(self) -> SessionStopController: ...


__all__ = [
    "SessionExecutionRuntimePort",
    "SessionExecutionServicePort",
    "SessionRunLoopRuntimePort",
    "SessionRunLoopServicePort",
    "SessionRunResultBuilderPort",
    "SessionRunResultPort",
    "SessionRunRuntimePort",
]
