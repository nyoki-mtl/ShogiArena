"""Engine participant implementation wrapping ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Sequence
from typing import Literal

from shogiarena.arena.engines.usi_engine import (
    AnalysisHandle,
    AsyncUsiEngine,
    PonderHandle,
    PonderHitTimings,
    UsiMateResult,
)
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkResult
from shogiarena.arena.execution.types import GameEngineProtocol, InfoHandler
from shogiarena.utils.types.types import GameResult

logger = logging.getLogger(__name__)


class EngineParticipant(GameEngineProtocol):
    """Wraps ``AsyncUsiEngine`` to expose the ``GameEngineProtocol`` interface."""

    def __init__(
        self,
        engine: AsyncUsiEngine,
        *,
        name_override: str | None = None,
        role: Literal["black", "white"] | None = None,
    ) -> None:
        self._engine = engine
        self._name_override = name_override
        self._prepared = False
        self._lock = asyncio.Lock()
        self._ponder_handle: PonderHandle | None = None
        self._ponder_predicted_move: str | None = None
        self._role: Literal["black", "white"] | None = role
        self._default_enable_early_ponder = getattr(engine.config, "enable_early_ponder", False)

    @property
    def name(self) -> str:
        return self._name_override or self._engine.name

    @property
    def options_name(self) -> str:
        """Return the stable engine name used for option snapshots."""

        name = self._engine.name
        if isinstance(name, str) and "#" in name:
            return name.split("#", 1)[0]
        return name

    async def prepare(self, *, initial_sfen: str) -> None:
        if not self._engine.is_running:
            await self._engine.start()
        await self._engine.new_game()
        await self._engine.submit_position(initial_sfen, ())
        self._prepared = True
        self._ponder_handle = None
        self._ponder_predicted_move = None

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        self._ensure_prepared()
        async with self._lock:
            return await self._engine.think(
                sfen=sfen,
                moves=tuple(moves),
                request=request,
                info_handler=info_handler,
                timeout=timeout,
            )

    async def think_mate(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        ply_limit: int | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult:
        self._ensure_prepared()
        async with self._lock:
            return await self._engine.think_mate(
                sfen=sfen,
                moves=tuple(moves),
                ply_limit=ply_limit,
                timeout=timeout,
            )

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> AnalysisHandle:
        self._ensure_prepared()
        async with self._lock:
            return await self._engine.analyze(
                sfen=sfen,
                moves=tuple(moves),
                request=request,
                info_handler=info_handler,
            )

    async def notify_gameover(self, result: GameResult) -> None:
        """Notify the underlying engine that the game has ended."""
        if not self._engine.is_running:
            await self.cancel_ponder()
            return

        token = self._map_game_result(result)
        if token is not None:
            await self._engine.gameover(token)
        else:
            logger.debug("%s: no gameover token mapped for result=%s", self.name, result.name)

        try:
            await self.cancel_ponder()
        finally:
            await self._engine.trigger_isready()

    async def stop(self) -> UsiThinkResult | None:
        if not self._engine.is_running:
            raise RuntimeError(f"Engine participant '{self.name}' is not running")
        async with self._lock:
            return await self._engine.stop()

    async def shutdown(self) -> None:
        try:
            await self.cancel_ponder()
            await self._engine.close()
        finally:
            self._prepared = False
            self._ponder_handle = None
            self._ponder_predicted_move = None

    def _ensure_prepared(self) -> None:
        if not self._prepared:
            raise RuntimeError(f"Engine participant '{self.name}' not prepared")

    def get_usi_options_snapshot(self) -> dict[str, dict[str, object]]:
        """Return a deep copy of the engine's reported USI options."""

        return copy.deepcopy(self._engine.get_usi_options())

    def get_engine_info_snapshot(self) -> dict[str, str]:
        """Return a shallow copy of engine-identification info from the USI handshake."""

        return dict(self._engine.engine_info)

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[str],
        request: UsiThinkRequest,
        predicted_move: str | None,
        info_handler: InfoHandler | None = None,
        enable_early_ponder: bool | None = None,
    ) -> None:
        if not predicted_move:
            return
        if not self._is_ponder_enabled():
            return
        self._ensure_prepared()
        early_flag = self._default_enable_early_ponder if enable_early_ponder is None else enable_early_ponder
        async with self._lock:
            await self._cancel_ponder_locked()
            handle = await self._engine.start_ponder(
                sfen=sfen,
                moves=tuple(moves),
                request=request,
                predicted_move=predicted_move,
                info_handler=info_handler,
                enable_early_ponder=early_flag,
            )
            self._ponder_handle = handle
            self._ponder_predicted_move = predicted_move

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResult | None:
        async with self._lock:
            if self._ponder_handle is None or not self._ponder_handle.active:
                return None
            handle = self._ponder_handle
            result = await handle.hit(timings=timings, timeout=timeout)
            self._ponder_handle = None
            self._ponder_predicted_move = None
            return result

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        async with self._lock:
            return await self._cancel_ponder_locked(timeout=timeout)

    def has_active_ponder(self) -> bool:
        return self._ponder_handle is not None and self._ponder_handle.active

    def active_ponder_predicted_move(self) -> str | None:
        return self._ponder_predicted_move if self.has_active_ponder() else None

    async def _cancel_ponder_locked(self, timeout: float | None = None) -> UsiThinkResult | None:
        if self._ponder_handle is None:
            return None
        handle = self._ponder_handle
        self._ponder_handle = None
        self._ponder_predicted_move = None
        return await handle.cancel(timeout=timeout)

    def _is_ponder_enabled(self) -> bool:
        options = self._engine.get_usi_options()
        if not options:
            return False
        opt = options.get("USI_Ponder")
        if opt is None:
            return False
        current = opt.get("current")
        if current is None:
            current = opt.get("default")
        if current is None:
            return False
        normalized = str(current).strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(f"Unsupported USI_Ponder value: '{current}'")

    def _map_game_result(self, result: GameResult) -> str | None:
        if result.is_draw():
            return "draw"
        if result.is_win():
            if self._role == "black":
                return "win" if result.is_black_win() else "lose"
            if self._role == "white":
                return "win" if result.is_white_win() else "lose"
            # Unknown role: treat any win as win for the notified engine? default to lose for safety
            return "lose" if result.is_white_win() else "win"
        # For non-win, non-draw outcomes (e.g., paused, error) fall back to loss
        return "lose"
