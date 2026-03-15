"""Run-loop orchestration for tournament sessions."""

from __future__ import annotations

import logging
from functools import partial
from typing import TypeVar

from shogiarena._core.contexts.game_session.application.session.reschedule_loop import (
    RescheduleAction,
    RescheduleDecision,
    RescheduleLoop,
)
from shogiarena._core.contexts.game_session.ports.run_runtime import SessionRunLoopRuntimePort
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import SessionStopControllerPort

TSessionContext = TypeVar("TSessionContext")

logger = logging.getLogger(__name__)


class TournamentRunLoopService:
    """Extracted state-transition logic for runner reschedule loop."""

    async def run_loop(
        self,
        runner: SessionRunLoopRuntimePort[TSessionContext],
        *,
        controller: SessionStopControllerPort,
        session_context: TSessionContext | None,
    ) -> None:
        loop: RescheduleLoop[SessionStopControllerPort] = RescheduleLoop()

        def on_reset(new_controller: SessionStopControllerPort) -> None:
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
        active_controller: SessionStopControllerPort,
        *,
        session_context: TSessionContext | None,
    ) -> None:
        if not runner.has_pending_games():
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
        active_controller: SessionStopControllerPort,
    ) -> RescheduleDecision:
        if runner.are_services_closed():
            runner.session_phase = "stopped"
            return RescheduleDecision(action=RescheduleAction.STOP)

        has_rescheduled = await runner.has_applied_pending_reschedule()
        if has_rescheduled:
            return RescheduleDecision(action=RescheduleAction.CONTINUE, should_reset_controller=True)

        if active_controller.is_stop_requested:
            if active_controller.reason == "cancelled":
                runner.session_phase = "waiting"
                return RescheduleDecision(action=RescheduleAction.WAIT, should_reset_controller=True)
            runner.session_phase = "stopping"
            return RescheduleDecision(action=RescheduleAction.STOP)

        if runner.has_pending_games():
            return RescheduleDecision(action=RescheduleAction.CONTINUE, should_reset_controller=True)

        if not runner.pending_reschedule and not active_controller.is_stop_requested:
            logger.debug("All scheduled games completed; stopping tournament runner")
            runner.should_stop_when_idle = True
            return RescheduleDecision(action=RescheduleAction.STOP)

        runner.session_phase = "waiting"
        return RescheduleDecision(action=RescheduleAction.WAIT, should_reset_controller=True)

    async def on_wait(self, runner: SessionRunLoopRuntimePort[TSessionContext]) -> None:
        await runner.wait_for_new_schedule()


__all__ = ["TournamentRunLoopService"]
