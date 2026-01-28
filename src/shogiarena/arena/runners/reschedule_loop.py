"""Helper for runner reschedule loops."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from shogiarena.arena.session import SessionStopController


class RescheduleAction(str, Enum):
    CONTINUE = "continue"
    WAIT = "wait"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class RescheduleDecision:
    action: RescheduleAction
    reset_controller: bool = False


class RescheduleLoop:
    """Run a loop with reschedule/wait logic."""

    async def run(
        self,
        *,
        controller: SessionStopController,
        run_iteration: Callable[[SessionStopController], Awaitable[None]],
        decide_next: Callable[[SessionStopController], Awaitable[RescheduleDecision]],
        on_wait: Callable[[], Awaitable[None]],
        on_reset: Callable[[SessionStopController], None] | None = None,
    ) -> None:
        active = controller
        while True:
            await run_iteration(active)
            decision = await decide_next(active)
            if decision.action is RescheduleAction.STOP:
                return

            if decision.reset_controller:
                active = SessionStopController()
                if on_reset is not None:
                    on_reset(active)

            if decision.action is RescheduleAction.WAIT:
                await on_wait()
                continue

            if decision.action is RescheduleAction.CONTINUE:
                continue

            raise RuntimeError(f"Unknown reschedule action: {decision.action!r}")
