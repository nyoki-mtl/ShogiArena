"""Search and game command helpers for ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from rshogi.core import Move

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    AnalysisHandle,
    InfoHandlerFn,
    UsiEngineState,
    UsiMateResult,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkResult

logger = logging.getLogger(__name__)

_GAMEOVER_STOP_WAIT_TIMEOUT_SECONDS = 1.0


class AsyncUsiEngineSearchMixin:
    _thinking_lock: asyncio.Lock
    _bestmove_future: asyncio.Future[UsiThinkResult] | None
    _mate_future: asyncio.Future[UsiMateResult] | None
    _analysis_handle: AnalysisHandle | None
    _analysis_request_id: int
    _ponder_handle: Any
    _is_closing: bool
    _state: UsiEngineState
    _should_wait_bestmove_after_mate: bool
    _pending_mate_result: UsiMateResult | None
    _info_handler: InfoHandlerFn | None
    _ignored_bestmove_count: int
    _handshake_timeout: float
    name: str
    is_running: bool

    config: Any
    _ensure_started: Any
    _ensure_state: Any
    _sync_ignored_bestmoves_before_go: Any
    _send_position: Any
    _send_command: Any
    _set_state: Any
    _reset_current_info: Any
    _clear_mate_tracking: Any
    _stop_without_wait: Any
    _set_future_exception: Any
    _clear_ponder_handle: Any
    _maybe_log_handshake_command: Any
    _recover_from_stop_timeout: Any
    trigger_isready: Any

    _abandon_future: Any

    async def submit_position(self, sfen: str, moves: Sequence[Move] | None = None) -> None:
        await self._ensure_started()
        await self._send_position(sfen, moves)

    async def new_game(self) -> None:
        await self._ensure_started()
        async with self._thinking_lock:
            self._ensure_state({UsiEngineState.READY, UsiEngineState.NOT_READY})
            if self._state != UsiEngineState.READY:
                await self.trigger_isready()
            self._maybe_log_handshake_command("usinewgame")
            await self._send_command("usinewgame")

    async def gameover(self, result: str) -> None:
        await self._ensure_started()
        normalized = result.strip().lower()
        if normalized not in {"win", "lose", "draw"}:
            raise ValueError(f"Unsupported gameover result: {result}")
        if not self.is_running:
            raise RuntimeError("Engine process is not running")

        async with self._thinking_lock:
            if self._is_closing:
                return

            if self._analysis_handle is not None:
                try:
                    await self._analysis_handle.stop()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    logger.exception("Error stopping analysis for %s during gameover: %s", self.name, exc)
                finally:
                    self._analysis_handle = None

            if self._ponder_handle is not None and self._ponder_handle.is_active:
                try:
                    await self._stop_without_wait()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    logger.exception("Error stopping ponder for %s during gameover: %s", self.name, exc)
                finally:
                    self._clear_ponder_handle()

            if self._mate_future and not self._mate_future.done():
                self._set_future_exception(self._mate_future, RuntimeError("Mate search aborted due to gameover"))
                self._mate_future = None
            self._clear_mate_tracking()

            if self._bestmove_future and not self._bestmove_future.done():
                await self._settle_bestmove_before_gameover(self._bestmove_future)

            self._reset_current_info()
            self._info_handler = None

            await self._send_command(f"gameover {normalized}")
            self._set_state(UsiEngineState.NOT_READY, reason=f"gameover {normalized} sent")

    async def _settle_bestmove_before_gameover(self, future: asyncio.Future[UsiThinkResult]) -> None:
        try:
            await self._stop_without_wait()
        except (TimeoutError, OSError, RuntimeError):
            logger.debug("[%s] failed to send stop during gameover", self.name, exc_info=True)

        timeout = min(self._handshake_timeout, _GAMEOVER_STOP_WAIT_TIMEOUT_SECONDS)
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError:
            self._recover_from_stop_timeout(future)
            return
        except (OSError, RuntimeError, ValueError):
            logger.debug("[%s] pending bestmove failed during gameover", self.name, exc_info=True)
        finally:
            if self._bestmove_future is future and future.done():
                self._bestmove_future = None
            self._info_handler = None
            self._reset_current_info()

    async def think(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Sequence[Move] | None = None,
        info_handler: InfoHandlerFn | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        await self._ensure_started()
        self._ensure_state({UsiEngineState.READY})
        async with self._thinking_lock:
            await self._sync_ignored_bestmoves_before_go()
            if self._bestmove_future is not None and not self._bestmove_future.done():
                raise RuntimeError("bestmove already pending")
            loop = asyncio.get_running_loop()
            self._bestmove_future = loop.create_future()
            self._reset_current_info()
            self._info_handler = info_handler
            future = self._bestmove_future
            try:
                await self._send_position(sfen, moves)
                self._maybe_log_handshake_command(request.to_command())
                await self._send_command(request.to_command())
                if request.is_ponder:
                    self._set_state(UsiEngineState.PONDER, reason="sent go ponder")
                else:
                    self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="sent go")
            except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
                self._abandon_future(future)
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
                    self._set_state(UsiEngineState.READY, reason="go command failed")
                task = asyncio.current_task()
                cancelling_requested = task.cancelling() > 0 if task is not None else False
                if self._is_closing or cancelling_requested:
                    logger.debug("[%s] go command aborted during shutdown: %s", self.name, exc)
                    raise asyncio.CancelledError() from None
                logger.warning("[%s] go command failed: %s", self.name, exc, exc_info=True)
                raise
            future = self._bestmove_future
            try:
                # Keep bestmove future pending on timeout so caller can recover via stop().
                result = await asyncio.wait_for(asyncio.shield(future), timeout)
                return result
            finally:
                if future is not None and future.done():
                    self._bestmove_future = None
                    self._info_handler = None
                    self._reset_current_info()
                    if self._state != UsiEngineState.WAITING_FOR_PONDER_BESTMOVE:
                        self._set_state(UsiEngineState.READY, reason="think completed")

    async def think_mate(
        self,
        *,
        sfen: str,
        ply_limit: int | None = None,
        node_limit: int | None = None,
        is_infinite: bool = False,
        moves: Sequence[Move] | None = None,
        info_handler: InfoHandlerFn | None = None,
        should_wait_for_bestmove: bool | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult:
        await self._ensure_started()
        self._ensure_state({UsiEngineState.READY})
        async with self._thinking_lock:
            await self._sync_ignored_bestmoves_before_go()
            if self._mate_future is not None and not self._mate_future.done():
                raise RuntimeError("mate search already pending")
            loop = asyncio.get_running_loop()
            self._mate_future = loop.create_future()
            self._pending_mate_result = None
            self._should_wait_bestmove_after_mate = (
                bool(self.config.should_mate_wait_for_bestmove)
                if should_wait_for_bestmove is None
                else bool(should_wait_for_bestmove)
            )
            command = self._build_mate_command(
                ply_limit=ply_limit,
                node_limit=node_limit,
                is_infinite=is_infinite,
            )
            self._reset_current_info()
            self._info_handler = info_handler
            future = self._mate_future
            try:
                await self._send_position(sfen, moves)
                self._maybe_log_handshake_command(command)
                await self._send_command(command)
                self._set_state(UsiEngineState.WAITING_FOR_CHECKMATE, reason="sent go mate")
            except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
                self._abandon_future(future)
                self._mate_future = None
                self._clear_mate_tracking()
                self._info_handler = None
                self._reset_current_info()
                if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
                    self._set_state(UsiEngineState.READY, reason="go mate failed")
                task = asyncio.current_task()
                cancelling_requested = task.cancelling() > 0 if task is not None else False
                if self._is_closing or cancelling_requested:
                    logger.debug("[%s] go mate aborted during shutdown: %s", self.name, exc)
                    raise asyncio.CancelledError() from None
                logger.warning("[%s] go mate failed: %s", self.name, exc, exc_info=True)
                raise
            future = self._mate_future
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout)
                return result
            finally:
                if future is not None and future.done():
                    self._mate_future = None
                    self._clear_mate_tracking()
                    self._info_handler = None
                    self._reset_current_info()
                    if self._state == UsiEngineState.WAITING_FOR_CHECKMATE:
                        self._set_state(UsiEngineState.READY, reason="mate search completed")

    def _build_mate_command(
        self,
        *,
        ply_limit: int | None,
        node_limit: int | None,
        is_infinite: bool,
    ) -> str:
        def coerce_positive_int(name: str, value: int | None) -> int | None:
            if value is None:
                return None
            if isinstance(value, bool):
                raise TypeError(f"{name} must be a positive integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise TypeError(f"{name} must be a positive integer") from exc
            if parsed <= 0:
                raise ValueError(f"{name} must be > 0")
            return parsed

        effective_ply_limit = coerce_positive_int("ply_limit", ply_limit)
        effective_node_limit = coerce_positive_int("node_limit", node_limit)
        if is_infinite and (effective_ply_limit is not None or effective_node_limit is not None):
            raise ValueError("infinite cannot be combined with ply_limit/node_limit")
        if effective_ply_limit is not None and effective_node_limit is not None:
            raise ValueError("Specify only one of ply_limit or node_limit")

        if not is_infinite and effective_ply_limit is None and effective_node_limit is None:
            default_ply = coerce_positive_int("mate_default_ply_limit", self.config.mate_default_ply_limit)
            default_nodes = coerce_positive_int("mate_default_node_limit", self.config.mate_default_node_limit)
            default_infinite = bool(self.config.is_mate_default_infinite)
            if default_infinite and (default_ply is not None or default_nodes is not None):
                raise ValueError(
                    "mate_default_infinite must not be combined with mate_default_ply_limit/mate_default_node_limit"
                )
            if default_ply is not None and default_nodes is not None:
                raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
            effective_ply_limit = default_ply
            effective_node_limit = default_nodes
            is_infinite = default_infinite

        if is_infinite:
            return "go mate infinite"
        if effective_node_limit is not None:
            return f"go mate nodes {effective_node_limit}"
        if effective_ply_limit is not None:
            return f"go mate {effective_ply_limit}"
        return "go mate"

    async def analyze(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Sequence[Move] | None = None,
        info_handler: InfoHandlerFn | None = None,
    ) -> AnalysisHandle:
        await self._ensure_started()
        if not request.is_infinite:
            raise ValueError("Analysis requires an infinite go request")
        if self._analysis_handle is not None:
            raise RuntimeError("Analysis already running")
        async with self._thinking_lock:
            self._ensure_state({UsiEngineState.READY})
            await self._sync_ignored_bestmoves_before_go()
            self._analysis_request_id += 1
            handle = AnalysisHandle(self, self._analysis_request_id)
            self._analysis_handle = handle
            self._info_handler = info_handler
            self._reset_current_info()
            try:
                await self._send_position(sfen, moves)
                await self._send_command(request.to_command())
                self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="analysis go infinite sent")
            except (TimeoutError, OSError, RuntimeError) as exc:
                self._analysis_handle = None
                self._info_handler = None
                self._reset_current_info()
                self._set_state(UsiEngineState.READY, reason="analysis go infinite failed")
                logger.warning("[%s] failed to start analysis: %s", self.name, exc, exc_info=True)
                raise
            return handle

    async def _stop_analysis(self, request_id: int) -> None:
        if self._analysis_handle is None or self._analysis_request_id != request_id:
            return
        try:
            await self._send_command("stop")
        finally:
            self._analysis_handle = None
            self._info_handler = None
            self._reset_current_info()
            if self._state == UsiEngineState.PONDER:
                self._set_state(UsiEngineState.WAITING_FOR_PONDER_BESTMOVE, reason="analysis stop during ponder")
