"""Standard session run flow for tournament/SPSA runners."""

from __future__ import annotations

from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import SessionRunnerPort

TRunResult = TypeVar("TRunResult")


class SessionFlow(Generic[TRunResult]):
    """Run the standard session flow for tournament/SPSA runners.

    Orchestrates the prepare -> init -> dashboard -> create -> run -> finalize
    lifecycle used by all session runners.
    """

    def __init__(self, runner: SessionRunnerPort[TRunResult]) -> None:
        self._runner = runner

    async def run(self) -> TRunResult | None:
        runner = self._runner

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
        hooks = runner.create_lifecycle_hooks(runner.stop_controller)
        runner.set_lifecycle_hooks(hooks)
        orchestrator = await runner.create_orchestrator(hooks, session_context)
        await runner.run_pre_orchestration_hooks(orchestrator)
        result = await runner.run_orchestrator(orchestrator, orchestrator.run())

        if runner.are_services_closed():
            return None

        final = await runner.finalize_and_persist(result)
        await runner.stop_services()
        return final


__all__ = ["SessionFlow"]
