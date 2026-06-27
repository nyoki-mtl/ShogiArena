"""Ponder start/stop helpers for ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from rshogi.core import Move

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.go_options import apply_go_options_defaults
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    InfoHandlerFn,
    PonderHandle,
    UsiEngineState,
    UsiMateResult,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkResult

logger = logging.getLogger(__name__)


class AsyncUsiEnginePonderMixin:
    _thinking_lock: asyncio.Lock
    _bestmove_future: asyncio.Future[UsiThinkResult] | None
    _mate_future: asyncio.Future[UsiMateResult] | None
    _ponder_handle: PonderHandle | None
    _ponder_request_id: int
    _info_handler: InfoHandlerFn | None
    _state: UsiEngineState
    _handshake_timeout: float
    name: str

    config: Any
    _ensure_started: Any
    _ensure_state: Any
    _sync_ignored_bestmoves_before_go: Any
    _send_position: Any
    _send_command: Any
    _set_state: Any
    _reset_current_info: Any
    _clear_ponder_handle: Any
    _stop_without_wait: Any
    _recover_from_stop_timeout: Any
    _recover_mate_from_stop_timeout: Any
    _clear_mate_tracking: Any

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[Move] | None,
        request: UsiThinkRequest,
        predicted_move: Move | None = None,
        info_handler: InfoHandlerFn | None = None,
        should_enable_early_ponder: bool | None = None,
    ) -> PonderHandle:
        await self._ensure_started()
        self._ensure_state({UsiEngineState.READY})
        if self._ponder_handle is not None:
            raise RuntimeError("Pondering already active")
        request = apply_go_options_defaults(request, getattr(self.config, "go_options", None))
        request_with_ponder = request if request.is_ponder else replace(request, is_ponder=True)
        sanitized_request = request_with_ponder
        should_require_timings = False
        if should_enable_early_ponder is None:
            should_enable_early_ponder = self.config.is_early_ponder_enabled
        has_clock_timings = any(
            value is not None
            for value in (
                request_with_ponder.btime,
                request_with_ponder.wtime,
                request_with_ponder.binc,
                request_with_ponder.winc,
                request_with_ponder.byoyomi,
            )
        )
        # Early ponder shifts clock-based timings from `go ponder` to `ponderhit`.
        # `movetime` cannot be shifted this way, so keep the original command.
        if should_enable_early_ponder and request_with_ponder.movetime is None and has_clock_timings:
            sanitized_request = replace(
                request_with_ponder,
                movetime=None,
                btime=None,
                wtime=None,
                binc=None,
                winc=None,
                byoyomi=None,
            )
            should_require_timings = True
        async with self._thinking_lock:
            await self._sync_ignored_bestmoves_before_go()
            if self._bestmove_future is not None and not self._bestmove_future.done():
                raise RuntimeError("Cannot start ponder while bestmove is pending")
            loop = asyncio.get_running_loop()
            self._bestmove_future = loop.create_future()
            self._reset_current_info()
            self._info_handler = info_handler
            self._ponder_request_id += 1
            handle = PonderHandle(
                self,
                self._ponder_request_id,
                predicted_move,
                should_require_timings=should_require_timings,
            )
            self._ponder_handle = handle
            try:
                await self._send_position(sfen, moves)
                await self._send_command(sanitized_request.to_command())
                self._set_state(UsiEngineState.PONDER, reason="sent go ponder")
            except (TimeoutError, OSError, RuntimeError) as exc:
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                self._clear_ponder_handle()
                logger.warning("[%s] failed to start ponder: %s", self.name, exc, exc_info=True)
                raise
        return handle

    async def stop(self, timeout: float | None = None) -> UsiThinkResult | None:
        await self._ensure_started()
        is_lock_acquired = False
        if not self._thinking_lock.locked():
            await self._thinking_lock.acquire()
            is_lock_acquired = True
        think_future: asyncio.Future[UsiThinkResult] | None = None
        mate_future: asyncio.Future[UsiMateResult] | None = None
        try:
            future = self._bestmove_future
            if future is not None and not future.done():
                think_future = future
            mate_candidate = self._mate_future
            if think_future is None and mate_candidate is not None and not mate_candidate.done():
                mate_future = mate_candidate
            if think_future is None and mate_future is None:
                await self._stop_without_wait()
                return None
            await self._stop_without_wait()
        finally:
            if is_lock_acquired:
                self._thinking_lock.release()

        if think_future is not None:
            is_timed_out = False
            try:
                result = await asyncio.wait_for(asyncio.shield(think_future), timeout or self._handshake_timeout)
                return result
            except TimeoutError:
                is_timed_out = True
                self._recover_from_stop_timeout(think_future)
                return None
            finally:
                if not is_timed_out:
                    self._bestmove_future = None
                    self._info_handler = None
                    self._reset_current_info()
                    self._clear_ponder_handle()

        if mate_future is not None:
            is_timed_out = False
            try:
                await asyncio.wait_for(asyncio.shield(mate_future), timeout or self._handshake_timeout)
            except TimeoutError:
                is_timed_out = True
                self._recover_mate_from_stop_timeout(mate_future)
                return None
            finally:
                if not is_timed_out and mate_future.done():
                    self._mate_future = None
                    self._clear_mate_tracking()
                    self._info_handler = None
                    self._reset_current_info()
                    if self._state == UsiEngineState.WAITING_FOR_CHECKMATE:
                        self._set_state(UsiEngineState.READY, reason="mate search stopped")

        return None
