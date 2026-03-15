"""Reschedule loop runtime primitives."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

TController = TypeVar("TController")


class RescheduleAction(str, Enum):
    CONTINUE = "continue"
    WAIT = "wait"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class RescheduleDecision:
    action: RescheduleAction
    should_reset_controller: bool = False


class RescheduleLoop(Generic[TController]):
    """Run a loop with reschedule/wait logic."""

    async def run(
        self,
        *,
        controller: TController,
        new_controller: Callable[[], TController],
        run_iteration: Callable[[TController], Awaitable[None]],
        decide_next: Callable[[TController], Awaitable[RescheduleDecision]],
        on_wait: Callable[[], Awaitable[None]],
        on_reset: Callable[[TController], None] | None = None,
    ) -> None:
        active = controller
        while True:
            await run_iteration(active)
            decision = await decide_next(active)
            if decision.action is RescheduleAction.STOP:
                return

            if decision.should_reset_controller:
                active = new_controller()
                if on_reset is not None:
                    on_reset(active)

            if decision.action is RescheduleAction.WAIT:
                await on_wait()
                continue

            if decision.action is RescheduleAction.CONTINUE:
                continue

            raise RuntimeError(f"Unknown reschedule action: {decision.action!r}")


__all__ = [
    "RescheduleAction",
    "RescheduleDecision",
    "RescheduleLoop",
]
