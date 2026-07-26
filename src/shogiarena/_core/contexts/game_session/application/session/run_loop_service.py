"""Run-loop orchestration for tournament sessions."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TypeVar

from shogiarena._core.contexts.game_session.application.session.reschedule_loop import (
    RescheduleAction,
    RescheduleDecision,
    RescheduleLoop,
)
from shogiarena._core.contexts.game_session.ports.run_runtime import SessionRunLoopRuntimePort
from shogiarena._core.shared.kernel.session_hooks import SessionStopController

TSessionContext = TypeVar("TSessionContext")

logger = logging.getLogger(__name__)


class TournamentRunLoopService:
    """Extracted state-transition logic for runner reschedule loop."""

    async def run_loop(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        *,
        controller: SessionStopController,
        session_context: TSessionContext | None,
    ) -> None:
        loop: RescheduleLoop[SessionStopController] = RescheduleLoop()

        def on_reset(new_controller: SessionStopController) -> None:
            runner.reset_stop_controller(new_controller)

        await loop.run(
            controller=controller,
            new_controller=lambda: type(controller)(),
            run_iteration=partial(self.run_iteration, runner, session_context=session_context),
            decide_next=partial(self.decide_next, runner),
            on_wait=partial(self.on_wait, runner),
            on_reset=on_reset,
        )

    async def run_iteration(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        active_controller: SessionStopController,
        *,
        session_context: TSessionContext | None,
    ) -> None:
        if not runner.has_pending_games():
            return
        if active_controller.is_stop_requested:
            # 反復に入る前から停止が決まっている場合は 1 局も投入しない（review 第4次 H1）。
            # loop は「まず 1 反復走らせてから停止を判定する」構造なので、ここで返さないと
            # resume で復元した停止判断（SPRT 決着済み、breaker 到達済み）が空振りする。
            logger.debug(
                "Stop already requested (%s) before dispatching; skipping this iteration", active_controller.reason
            )
            runner.session_phase = "stopping"
            return
        runner.session_phase = "running"
        hooks = runner.create_lifecycle_hooks(active_controller)
        runner.set_lifecycle_hooks(hooks)
        orchestrator = await runner.create_orchestrator(hooks, session_context)
        await runner.run_pre_orchestration_hooks(orchestrator)
        await runner.run_orchestrator(orchestrator, orchestrator.run())
        runner.session_phase = "draining"

    async def decide_next(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        active_controller: SessionStopController,
    ) -> RescheduleDecision:
        if runner.are_services_closed():
            runner.session_phase = "stopped"
            return RescheduleDecision(action=RescheduleAction.STOP)

        # run を終わらせる停止（safety breaker、SPRT 決着、OpenBench 停止指示）は
        # pending reschedule より優先する（review 第5次 H1）。先に reschedule を適用すると
        # controller が作り直され、停止ごと消えて次の反復で dispatch できてしまう。
        #
        # 判定には reason 文字列ではなく ``is_terminal`` を使う（review 第6次）。
        # reason は診断ラベルであり、後から来た pause 要求（cancel）で置き換わりうる。
        # 制御をラベルに依存させると、確定した停止が cancel で解除される。
        if active_controller.is_terminal:
            if runner.pending_reschedule:
                logger.warning(
                    "Dropping a pending reschedule because the run is stopping (%s)",
                    active_controller.reason,
                )
            runner.session_phase = "stopping"
            return RescheduleDecision(action=RescheduleAction.STOP)

        has_rescheduled = await runner.has_applied_pending_reschedule()
        if has_rescheduled:
            return RescheduleDecision(action=RescheduleAction.CONTINUE, should_reset_controller=True)

        if active_controller.is_stop_requested:
            # 残るのは pause（利用者の一時停止、または schedule 差し替え待ち）。
            runner.session_phase = "waiting"
            return RescheduleDecision(action=RescheduleAction.WAIT, should_reset_controller=True)

        if runner.has_pending_games():
            return RescheduleDecision(action=RescheduleAction.CONTINUE, should_reset_controller=True)

        if not runner.pending_reschedule and not active_controller.is_stop_requested:
            logger.debug("All scheduled games completed; stopping tournament runner")
            runner.should_stop_when_idle = True
            return RescheduleDecision(action=RescheduleAction.STOP)

        runner.session_phase = "waiting"
        return RescheduleDecision(action=RescheduleAction.WAIT, should_reset_controller=True)

    async def on_wait(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        active_controller: SessionStopController,
    ) -> None:
        """新 schedule または停止要求のどちらかを待つ。

        controller は WAIT へ入る直前に作り直される。OpenBench heartbeat がその新しい
        controller へ terminal stop を立てても、schedule event だけを待つ実装では
        loop が起床せず停止を確定できない。
        """

        schedule_wait = asyncio.create_task(runner.wait_for_new_schedule())
        stop_wait = asyncio.create_task(active_controller.wait_until_stop_requested())
        tasks = (schedule_wait, stop_wait)
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                await task
        finally:
            # Parent cancellation can arrive while asyncio.wait() is suspended. Always
            # reap both children; otherwise a stale schedule waiter can wake after the
            # session has already finalized.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["TournamentRunLoopService"]
