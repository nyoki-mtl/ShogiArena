"""Standard session run flow for tournament/SPSA runners."""

from __future__ import annotations

import logging
from typing import Generic, TypeVar

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import SessionRunnerPort

TRunResult = TypeVar("TRunResult")

logger = logging.getLogger(__name__)


class SessionFlow(Generic[TRunResult]):
    """Run the standard session flow for tournament/SPSA runners.

    Orchestrates the prepare -> init -> dashboard -> create -> run -> finalize
    lifecycle used by all session runners.
    """

    def __init__(self, runner: SessionRunnerPort[TRunResult]) -> None:
        self._runner = runner

    async def run(self) -> TRunResult | None:
        # prepare 中の失敗（dashboard 起動、session context 構築など）でも service を残さない。
        # ``init_services`` の後で落ちると、包まない限り dashboard や DB が開いたままになる
        # （decisions.md Decision 9: 両 run path・全例外経路で cleanup）。
        try:
            return await self._run_flow()
        except BaseException:
            await self._stop_services_quietly()
            raise

    async def _run_flow(self) -> TRunResult | None:
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

        # finalize 中の例外でも service cleanup を必ず走らせる（decisions.md Decision 9）。
        # ``stop_services`` は idempotent なので、内部で既に停止済みでも二重実行にならない。
        try:
            final = await runner.finalize_and_persist(result)
        finally:
            await runner.stop_services()
        return final

    async def _stop_services_quietly(self) -> None:
        """cleanup の失敗で元例外を隠さない。"""
        try:
            await self._runner.stop_services()
        except BaseException as exc:  # noqa: BLE001 - 元例外を優先するため握って記録する
            logger.error("Service cleanup failed while handling a session failure: %s", exc, exc_info=True)


__all__ = ["SessionFlow"]
