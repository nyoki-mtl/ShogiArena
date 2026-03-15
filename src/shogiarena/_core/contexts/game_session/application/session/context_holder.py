"""Session context holder for managing session lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field

from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks, SessionStopController


@dataclass
class SessionContextHolder:
    """Mutable holder for session context, hooks, and stop controller."""

    context: SessionContext | None = None
    hooks: GameLifecycleHooks | None = None
    stop_controller: SessionStopController = field(default_factory=SessionStopController)

    def set_context(self, session_context: SessionContext | None) -> None:
        self.context = session_context

    def set_hooks(self, hooks: GameLifecycleHooks) -> None:
        self.hooks = hooks

    def reset_stop_controller(self, controller: SessionStopController) -> None:
        self.stop_controller = controller


__all__ = ["SessionContextHolder"]
