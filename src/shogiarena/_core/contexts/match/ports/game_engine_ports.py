"""Execution-time engine protocol interfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from rshogi.core import Move

from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings, UsiThinkRequest, UsiThinkResultPort
from shogiarena._core.shared.kernel.engine_io import UsiIoEvent
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject

InfoHandler = Callable[[Any], Awaitable[None] | None]


@runtime_checkable
class GameEnginePort(Protocol):
    """Minimal interface required by ``GameRunner`` for engine participants."""

    @property
    def name(self) -> str: ...

    async def prepare(self, *, initial_sfen: str) -> None: ...

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResultPort: ...

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        ply_limit: int | None = None,
        node_limit: int | None = None,
        is_infinite: bool = False,
        info_handler: InfoHandler | None = None,
        should_wait_for_bestmove: bool | None = None,
        timeout: float | None = None,
    ) -> Any: ...

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> Any: ...

    async def notify_gameover(self, result: GameResult) -> None: ...

    async def stop(self) -> UsiThinkResultPort | None: ...

    async def shutdown(self) -> None: ...

    def get_usi_options_snapshot(self) -> dict[str, JsonObject]: ...

    def get_engine_info_snapshot(self) -> dict[str, str]: ...

    def register_io_log_handler(
        self,
        handler: Callable[[UsiIoEvent], Awaitable[None] | None],
    ) -> Callable[[], None]: ...

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        predicted_move: Move | None,
        info_handler: InfoHandler | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> None: ...

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResultPort | None: ...

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResultPort | None: ...

    def has_active_ponder(self) -> bool: ...

    def active_ponder_predicted_move(self) -> Move | None: ...
