"""Engine participant implementation wrapping ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from rshogi.core import Move
from rshogi.types import Color

from shogiarena._core.contexts.match.ports.game_engine_ports import GameEnginePort, InfoHandler, JsonObject
from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings, UsiThinkRequest, UsiThinkResultPort
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str

logger = logging.getLogger(__name__)


@runtime_checkable
class _PonderHandlePort(Protocol):
    is_active: bool

    async def hit(
        self,
        *,
        timings: PonderHitTimings | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResultPort: ...

    async def cancel(self, *, timeout: float | None = None) -> UsiThinkResultPort | None: ...


@runtime_checkable
class _EngineConfigPort(Protocol):
    is_early_ponder_enabled: bool


@runtime_checkable
class _EngineRuntimePort(Protocol):
    config: _EngineConfigPort
    name: str
    is_running: bool
    engine_info: dict[str, str]

    async def start(self) -> None: ...
    async def trigger_isready(self) -> None: ...
    async def new_game(self) -> None: ...
    async def submit_position(self, sfen: str, moves: Sequence[Move]) -> None: ...
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
    async def gameover(self, result: str) -> None: ...
    async def stop(self, timeout: float | None = None) -> UsiThinkResultPort | None: ...
    async def close(self) -> None: ...
    def get_usi_options(self) -> Mapping[str, JsonObject]: ...
    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        predicted_move: Move | None = None,
        info_handler: InfoHandler | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> _PonderHandlePort: ...
    def register_io_log_handler(
        self,
        handler: Callable[[JsonObject], Awaitable[None] | None],
    ) -> Callable[[], None]: ...


class EngineParticipant(GameEnginePort):
    """Wraps ``AsyncUsiEngine`` to expose the ``GameEnginePort`` interface."""

    def __init__(
        self,
        engine: object,
        *,
        name_override: str | None = None,
        role: Color | None = None,
    ) -> None:
        if not isinstance(engine, _EngineRuntimePort):
            raise TypeError("engine must satisfy _EngineRuntimePort")
        self._engine = engine
        self._name_override = name_override
        self._is_prepared = False
        self._lock = asyncio.Lock()
        self._ponder_handle: _PonderHandlePort | None = None
        self._ponder_predicted_move: Move | None = None
        self._role: Color | None = role
        self._is_early_ponder_enabled_by_default = self._engine.config.is_early_ponder_enabled

    def register_io_log_handler(
        self,
        handler: Callable[[JsonObject], Awaitable[None] | None],
    ) -> Callable[[], None]:
        return self._engine.register_io_log_handler(handler)

    @property
    def name(self) -> str:
        return self._name_override or self._engine.name

    @property
    def options_name(self) -> str:
        """Return the stable engine name used for option snapshots."""

        name = self._engine.name
        if "#" in name:
            return name.split("#", 1)[0]
        return name

    async def prepare(self, *, initial_sfen: str) -> None:
        await self.prepare_ready_state()
        await self.prepare_new_game_position(initial_sfen=initial_sfen)

    async def prepare_ready_state(self) -> None:
        if not self._engine.is_running:
            await self._engine.start()
        else:
            await self._engine.trigger_isready()

    async def prepare_new_game_position(self, *, initial_sfen: str) -> None:
        await self._engine.new_game()
        await self._engine.submit_position(initial_sfen, ())
        self._is_prepared = True
        self._ponder_handle = None
        self._ponder_predicted_move = None

    async def think(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResultPort:
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
        moves: Sequence[Move],
        ply_limit: int | None = None,
        node_limit: int | None = None,
        is_infinite: bool = False,
        info_handler: InfoHandler | None = None,
        should_wait_for_bestmove: bool | None = None,
        timeout: float | None = None,
    ) -> Any:
        self._ensure_prepared()
        async with self._lock:
            return await self._engine.think_mate(
                sfen=sfen,
                moves=tuple(moves),
                ply_limit=ply_limit,
                node_limit=node_limit,
                is_infinite=is_infinite,
                info_handler=info_handler,
                should_wait_for_bestmove=should_wait_for_bestmove,
                timeout=timeout,
            )

    async def analyze(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> Any:
        self._ensure_prepared()
        async with self._lock:
            return await self._engine.analyze(
                sfen=sfen,
                moves=tuple(moves),
                request=request,
                info_handler=info_handler,
            )

    async def notify_gameover(self, result: GameResult) -> None:
        """Notify the underlying engine that the game has ended.

        Keep this path short so orchestrators can promptly release shared
        instance capacity (slots/max_engines) after a game ends. Readiness for
        the next game is established in ``prepare_ready_state()``.
        """
        if not self._engine.is_running:
            await self.cancel_ponder()
            return

        token = self._map_game_result(result)
        if token is not None:
            await self._engine.gameover(token)
        else:
            logger.debug("%s: no gameover token mapped for result=%s", self.name, result.name)

        await self.cancel_ponder()

    async def stop(self) -> UsiThinkResultPort | None:
        if not self._engine.is_running:
            raise RuntimeError(f"Engine participant '{self.name}' is not running")
        async with self._lock:
            return await self._engine.stop()

    async def shutdown(self) -> None:
        try:
            await self.cancel_ponder()
            await self._engine.close()
        finally:
            self._is_prepared = False
            self._ponder_handle = None
            self._ponder_predicted_move = None

    def _ensure_prepared(self) -> None:
        if not self._is_prepared:
            raise RuntimeError(f"Engine participant '{self.name}' not prepared")

    def get_usi_options_snapshot(self) -> dict[str, JsonObject]:
        """Return a deep copy of the engine's reported USI options."""

        snapshot: dict[str, JsonObject] = {}
        for option_name, option_payload in self._engine.get_usi_options().items():
            cloned_payload = copy.deepcopy(option_payload)
            if isinstance(cloned_payload, dict):
                snapshot[option_name] = {str(key): value for key, value in cloned_payload.items()}
        return snapshot

    def get_engine_info_snapshot(self) -> dict[str, str]:
        """Return a shallow copy of engine-identification info from the USI handshake."""

        return dict(self._engine.engine_info)

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move],
        request: UsiThinkRequest,
        predicted_move: Move | None,
        info_handler: InfoHandler | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> None:
        if predicted_move is None:
            return
        if not self._is_ponder_enabled():
            return
        self._ensure_prepared()
        early_flag = (
            self._is_early_ponder_enabled_by_default
            if should_enable_early_ponder is None
            else should_enable_early_ponder
        )
        async with self._lock:
            await self._cancel_ponder_locked()
            handle = await self._engine.start_ponder(
                sfen=sfen,
                moves=tuple(moves),
                request=request,
                predicted_move=predicted_move,
                info_handler=info_handler,
                should_enable_early_ponder=early_flag,
            )
            self._ponder_handle = handle
            self._ponder_predicted_move = predicted_move

    async def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None,
        timeout: float | None = None,
    ) -> UsiThinkResultPort | None:
        async with self._lock:
            if self._ponder_handle is None or not self._ponder_handle.is_active:
                self._ponder_handle = None
                self._ponder_predicted_move = None
                return None
            handle = self._ponder_handle
            result = await handle.hit(timings=timings, timeout=timeout)
            self._ponder_handle = None
            self._ponder_predicted_move = None
            return result

    async def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResultPort | None:
        async with self._lock:
            return await self._cancel_ponder_locked(timeout=timeout)

    def has_active_ponder(self) -> bool:
        return self._ponder_handle is not None and self._ponder_handle.is_active

    def active_ponder_predicted_move(self) -> Move | None:
        return self._ponder_predicted_move if self.has_active_ponder() else None

    async def _cancel_ponder_locked(self, timeout: float | None = None) -> UsiThinkResultPort | None:
        if self._ponder_handle is None:
            return None
        handle = self._ponder_handle
        if not handle.is_active:
            self._ponder_handle = None
            self._ponder_predicted_move = None
            return None
        self._ponder_handle = None
        self._ponder_predicted_move = None
        return await handle.cancel(timeout=timeout)

    def _is_ponder_enabled(self) -> bool:
        options = self._engine.get_usi_options()
        if not options:
            return True
        opt = options.get("USI_Ponder")
        if opt is None:
            return True
        current = opt.get("current")
        if current is None:
            current = opt.get("default")
        if current is None:
            return True
        normalized = (coerce_str(current) or "").strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        logger.warning("%s: unsupported USI_Ponder value '%s'; treating as disabled", self.name, current)
        return False

    def _map_game_result(self, result: GameResult) -> str | None:
        """Map a decided game result to a USI ``gameover`` token from this engine's perspective.

        Returns ``None`` for non-decided outcomes (paused/error/invalid): there is no valid
        gameover token for them, and the caller skips the notification. A win/loss with an
        unknown role is a wiring bug and fails fast rather than guessing a token.
        """
        if result.is_draw():
            return "draw"
        if result.is_win():
            if self._role is None:
                raise ValueError(f"{self.name}: cannot map a decisive result without an engine role")
            if self._role.is_black():
                return "win" if result.is_black_win() else "lose"
            return "win" if result.is_white_win() else "lose"
        # Paused/error/invalid are not games with a win/lose/draw token; skip the gameover.
        return None
