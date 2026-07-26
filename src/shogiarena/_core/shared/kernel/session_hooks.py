"""Shared session lifecycle hook contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, cast

import rsshogi.record

# 停止には性質の異なる 2 種類がある（task 0052 / review 第6次）。
#
# - **pause**: run はまだ続く。新しい schedule を待つ、あるいは reschedule を適用して再開する。
# - **terminal**: run を終わらせる。reschedule で再開させてはならない。
#
# reason 文字列 1 つで「診断ラベル」と「pause / terminal の制御」を兼ねると、
# 片方の都合で他方が壊れる。実際、pause を `cancelled` だけと見なした結果、
# production の `reschedule` が terminal 扱いされて破棄された。
# ここでは制御を集合で、ラベルを優先順位で、それぞれ独立に決める。
_PAUSE_STOP_REASON_PRECEDENCE: dict[str, int] = {
    "cancelled": 20,
    "reschedule": 10,
}

_TERMINAL_STOP_REASON_PRECEDENCE: dict[str, int] = {
    # 安全停止。測定条件そのものが壊れている。
    "timeout-burst": 90,
    "timeout-attribution-unknown": 90,
    "transport-timeout": 90,
    # OpenBench 側の異常と停止指示。
    "openbench-heartbeat-error": 80,
    "openbench-stop": 70,
    # 正常な早期終了。terminal の中では最も弱い。
    "sprt-finished": 60,
}

# 認識できない理由（および reason 無しの停止）は terminal として扱う。
# 判別できない停止で run を待たせ続けるより、止める方が安全側に倒れる。
_UNRECOGNIZED_TERMINAL_PRECEDENCE = 50

STOP_REASON_PRECEDENCE: dict[str, int] = {
    **_TERMINAL_STOP_REASON_PRECEDENCE,
    **_PAUSE_STOP_REASON_PRECEDENCE,
}


class SessionStopController:
    """Coordinator that tracks whether new games should be scheduled."""

    __slots__ = ("_is_stop_requested", "_is_terminal", "_reason", "_stop_requested_event")

    def __init__(self) -> None:
        self._is_stop_requested: bool = False
        self._reason: str | None = None
        self._is_terminal: bool = False
        self._stop_requested_event = asyncio.Event()

    def request_stop(self, *, reason: str | None = None) -> None:
        """Mark the session as stopped and optionally record a reason.

        理由のラベルは ``STOP_REASON_PRECEDENCE`` で **強い方** を残す。後勝ちにすると、
        同じ状態でも要求順で terminal reason が変わってしまう（live は breaker → SPRT、
        resume は SPRT → breaker の順に評価する）。同順位なら先に記録した方を残す。

        ``is_terminal`` は **一度立ったら下がらない**。run を終わらせると決めた後に
        pause 要求（cancel、reschedule）が来ても、その停止を解除させないため。
        """

        self._is_stop_requested = True
        self._stop_requested_event.set()
        if not is_terminal_stop_reason(reason):
            # pause 要求。terminal を解除しないし、terminal のラベルも上書きしない。
            if self._is_terminal:
                return
            if self._reason is None or _stop_reason_precedence(reason or "") > _stop_reason_precedence(self._reason):
                self._reason = reason
            return

        self._is_terminal = True
        if reason is None:
            # 理由の無い terminal 停止。pause のラベルが残っていると、制御状態と
            # 診断ラベルが食い違う（review 第7次 L3）。古い pause ラベルは落とす。
            if is_pause_stop_reason(self._reason):
                self._reason = None
            return
        if self._reason is None or _stop_reason_precedence(reason) > _stop_reason_precedence(self._reason):
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

    @property
    def is_terminal(self) -> bool:
        """run を終わらせる停止か（pending reschedule で再開させてはならないか）。

        一度立ったら下がらない。制御はこの値で判断し、reason 文字列では判断しない。
        """
        return self._is_terminal

    async def wait_until_stop_requested(self) -> None:
        """停止要求が立つまで待つ。

        run loop が新しい schedule を待っている間にも、OpenBench heartbeat などの
        長寿命 producer から terminal stop が届く。schedule event だけを待つと
        terminal stop を受け取っても loop が起床しないため、同時にこの event を待つ。
        """

        await self._stop_requested_event.wait()


def _stop_reason_precedence(reason: str) -> int:
    return STOP_REASON_PRECEDENCE.get(reason, _UNRECOGNIZED_TERMINAL_PRECEDENCE)


def is_pause_stop_reason(reason: str | None) -> bool:
    """停止が「run を続けたまま待つ」種類か。

    利用者による一時停止（``cancelled``）と、schedule 差し替えのための停止（``reschedule``）。
    これ以外は run を終わらせる停止として扱う。
    """
    return reason in _PAUSE_STOP_REASON_PRECEDENCE


def is_terminal_stop_reason(reason: str | None) -> bool:
    """停止が run を終わらせる種類か。

    認識できない理由と reason 無しの停止も terminal とする。判別できない停止で
    run を待たせ続けるより、止める方が安全側に倒れる。
    """
    return not is_pause_stop_reason(reason)


class GameCompletionPayload(Protocol):
    """Marker protocol for payloads passed to game completion hooks."""


PayloadT = TypeVar("PayloadT", bound=GameCompletionPayload, covariant=True)


@dataclass
class GameCompletionEvent(Generic[PayloadT]):
    """Domain-agnostic payload passed to lifecycle hooks on game completion."""

    game_id: str
    game_info: rsshogi.record.Record
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
        # payload の型は直前の isinstance で実行時保証済み。event 自体は不変ジェネリックのため
        # 絞り込みが伝播せず、同一オブジェクトのまま型だけを付け替える。
        narrowed_event = cast("GameCompletionEvent[_PayloadT_inv]", event)
        await self._on_game_complete_fn(narrowed_event)


__all__ = [
    "STOP_REASON_PRECEDENCE",
    "CallbackGameLifecycleHooks",
    "GameCompletionEvent",
    "GameCompletionPayload",
    "GameLifecycleHooks",
    "NoopGameLifecycleHooks",
    "PayloadT",
    "SessionStopController",
    "is_pause_stop_reason",
    "is_terminal_stop_reason",
]
