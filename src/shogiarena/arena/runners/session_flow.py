"""Session flow orchestration for session runners."""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol

from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator
from shogiarena.arena.session import GameLifecycleHooks, SessionContext, SessionStopController


class SessionRunnerProtocol(Protocol):
    async def prepare_run_dir(self) -> None: ...

    async def prepare_domain(self) -> None: ...

    async def init_services(self) -> None: ...

    def get_dashboard_params(self) -> tuple[Path, int, int] | None: ...

    async def start_dashboard_server(self, run_dir: Path, preferred_port: int, num_workers: int) -> int: ...

    async def seed_initial_summary(self) -> None: ...

    def build_session_context(self) -> SessionContext | None: ...

    def set_session_context(self, session_context: SessionContext | None) -> None: ...

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks: ...

    def set_lifecycle_hooks(self, hooks: GameLifecycleHooks) -> None: ...

    async def create_orchestrator(
        self, hooks: GameLifecycleHooks, session_context: SessionContext | None
    ) -> BaseOrchestrator: ...

    async def run_pre_orchestration_hooks(self, orchestrator: BaseOrchestrator) -> None: ...

    async def run_orchestrator(self, orchestrator: BaseOrchestrator, run_coro: Awaitable[Any]) -> Any | None: ...

    async def finalize_and_persist(self, run_result: Any | None) -> Any | None: ...

    async def stop_services(self) -> None: ...

    def services_closed(self) -> bool: ...

    @property
    def stop_controller(self) -> SessionStopController: ...


class SessionFlow:
    """Run the standard session flow for tournament/SPSA runners."""

    def __init__(self, runner: SessionRunnerProtocol) -> None:
        self._runner = runner

    async def run(self) -> Any | None:
        runner = self._runner

        await runner.prepare_run_dir()
        await runner.prepare_domain()
        await runner.init_services()

        dash = runner.get_dashboard_params()
        if dash is not None:
            run_dir, port, num_workers = dash
            await runner.start_dashboard_server(run_dir, port, num_workers)
            await runner.seed_initial_summary()

        session_context = runner.build_session_context()
        runner.set_session_context(session_context)
        hooks = runner.create_lifecycle_hooks(runner.stop_controller)
        runner.set_lifecycle_hooks(hooks)
        orchestrator = await runner.create_orchestrator(hooks, session_context)
        await runner.run_pre_orchestration_hooks(orchestrator)
        result = await runner.run_orchestrator(orchestrator, orchestrator.run())

        if runner.services_closed():
            return None

        final = await runner.finalize_and_persist(result)
        await runner.stop_services()
        return final
