"""Execution-time engine protocol interfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from rshogi.core import Move

from shogiarena.arena.engines.usi_engine import AnalysisHandle, PonderHitTimings, UsiMateResult
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult
from shogiarena.utils.types.types import GameResult

InfoHandler = Callable[[UsiThinkPV], Awaitable[None] | None]


@runtime_checkable
class GameEngineProtocol(Protocol):
    """Minimal interface required by ``GameRunner`` for engine participants."""

    @property
    def name(self) -> str: ...

    async def prepare(self, *, initial_sfen: str) -> None: ...

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult: ...

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        ply_limit: int | None = None,
        node_limit: int | None = None,
        infinite: bool = False,
        info_handler: InfoHandler | None = None,
        wait_for_bestmove: bool | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult: ...

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> AnalysisHandle: ...

    async def notify_gameover(self, result: GameResult) -> None: ...

    async def stop(self) -> UsiThinkResult | None: ...

    async def shutdown(self) -> None: ...

    def get_usi_options_snapshot(self) -> dict[str, dict[str, object]]: ...

    def get_engine_info_snapshot(self) -> dict[str, str]: ...

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        predicted_move: Move | None,
        info_handler: InfoHandler | None = None,
        enable_early_ponder: bool | None = None,
    ) -> None: ...

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResult | None: ...

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None: ...

    def has_active_ponder(self) -> bool: ...

    def active_ponder_predicted_move(self) -> Move | None: ...
