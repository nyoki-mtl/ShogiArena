"""Shared session lifecycle hook contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

import rshogi.record


class SessionStopController:
    """Coordinator that tracks whether new games should be scheduled."""

    __slots__ = ("_is_stop_requested", "_reason")

    def __init__(self) -> None:
        self._is_stop_requested: bool = False
        self._reason: str | None = None

    def request_stop(self, *, reason: str | None = None) -> None:
        """Mark the session as stopped and optionally record a reason."""

        self._is_stop_requested = True
        if reason is not None:
            self._reason = reason

    def should_continue(self) -> bool:
        """Return ``True`` if scheduling should continue."""

        return not self._is_stop_requested

    @property
    def reason(self) -> str | None:
        """Return the recorded stop reason, if any."""

        return self._reason

    @property
    def is_stop_requested(self) -> bool:
        return self._is_stop_requested


class GameCompletionPayload(Protocol):
    """Marker protocol for payloads passed to game completion hooks."""


PayloadT = TypeVar("PayloadT", bound=GameCompletionPayload, covariant=True)


@dataclass
class GameCompletionEvent(Generic[PayloadT]):
    """Domain-agnostic payload passed to lifecycle hooks on game completion."""

    game_id: str
    game_info: rshogi.record.GameRecord
    payload: PayloadT
    worker_idx: int | None = None
    is_stop_requested: bool = False


class GameLifecycleHooks(Protocol):
    """Protocol for runner-provided lifecycle hooks."""

    async def on_game_complete(self, event: GameCompletionEvent[GameCompletionPayload]) -> None:
        """Handle a completed game emitted by an orchestrator."""

    async def should_continue(self) -> bool:
        """Return whether orchestrator should continue scheduling new games."""


class NoopGameLifecycleHooks(GameLifecycleHooks):
    """No-op hooks backed by an optional stop controller."""

    def __init__(self, stop_controller: SessionStopController | None = None) -> None:
        self._stop_controller = stop_controller or SessionStopController()

    @property
    def stop_controller(self) -> SessionStopController:
        return self._stop_controller

    async def on_game_complete(self, event: GameCompletionEvent[GameCompletionPayload]) -> None:
        _ = event
        return

    async def should_continue(self) -> bool:
        return self._stop_controller.should_continue()


_PayloadT_inv = TypeVar("_PayloadT_inv", bound=GameCompletionPayload)


class CallbackGameLifecycleHooks(NoopGameLifecycleHooks, Generic[_PayloadT_inv]):
    """Reusable callback-based lifecycle hooks.

    Replaces per-runner nested lifecycle classes by accepting a payload type
    and a typed completion handler callback.  The runner registers its bound
    handler at construction time and no subclassing is needed.
    """

    def __init__(
        self,
        *,
        stop_controller: SessionStopController,
        payload_type: type[_PayloadT_inv],
        on_game_complete_fn: Callable[[GameCompletionEvent[_PayloadT_inv]], Awaitable[None]],
    ) -> None:
        super().__init__(stop_controller)
        self._payload_type = payload_type
        self._on_game_complete_fn = on_game_complete_fn

    async def on_game_complete(self, event: GameCompletionEvent[GameCompletionPayload]) -> None:
        payload = event.payload
        if not isinstance(payload, self._payload_type):
            raise TypeError(f"expected {self._payload_type.__name__}, got {type(payload).__name__}")
        await self._on_game_complete_fn(event)  # type: ignore[arg-type]


__all__ = [
    "CallbackGameLifecycleHooks",
    "GameCompletionEvent",
    "GameCompletionPayload",
    "GameLifecycleHooks",
    "NoopGameLifecycleHooks",
    "PayloadT",
    "SessionStopController",
]
