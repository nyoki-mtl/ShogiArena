from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shogiarena.records import GameInfo

from .state import SessionStopController


@dataclass
class GameCompletionEvent:
    """Domain-agnostic payload passed to lifecycle hooks on game completion."""

    game_id: str
    game_info: GameInfo
    payload: Any
    worker_idx: int | None = None
    stop_requested: bool = False


class GameLifecycleHooks(Protocol):
    """Protocol for runner-provided lifecycle hooks."""

    async def on_game_complete(self, event: GameCompletionEvent) -> None:
        """Handle a completed game emitted by an orchestrator."""

    async def should_continue(self) -> bool:
        """Return whether orchestrator should continue scheduling new games."""


class LifecycleHooksBase(GameLifecycleHooks):
    """No-op hooks backed by an optional stop controller."""

    def __init__(self, stop_controller: SessionStopController | None = None) -> None:
        self._stop_controller = stop_controller or SessionStopController()

    @property
    def stop_controller(self) -> SessionStopController:
        return self._stop_controller

    async def on_game_complete(self, event: GameCompletionEvent) -> None:  # noqa: D401
        return

    async def should_continue(self) -> bool:  # noqa: D401
        return self._stop_controller.should_continue()


__all__ = [
    "GameCompletionEvent",
    "GameLifecycleHooks",
    "LifecycleHooksBase",
]
