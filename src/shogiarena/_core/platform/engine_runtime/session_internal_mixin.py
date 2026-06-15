"""Internal state-transition helpers for ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from rshogi.core import Move

from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    _READY_TIMEOUT_DEFAULT,
    _STALE_BESTMOVE_SYNC_TIMEOUT_SECONDS,
    ReadyTimeout,
    TFutureResult,
    UsiEngineState,
    UsiMateResult,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkResult

logger = logging.getLogger(__name__)


class AsyncUsiEngineInternalMixin:
    _is_started: bool
    _handshake_timeout: float
    _ignored_bestmove_count: int
    _bestmove_future: asyncio.Future[UsiThinkResult] | None
    _mate_future: asyncio.Future[UsiMateResult] | None
    _info_handler: Any
    _state: UsiEngineState
    _readyok_future: asyncio.Future[None] | None
    _ponder_handle: Any
    _ponder_request_id: int
    _last_sent_command: str | None
    name: str
    _process: Any

    _send_command: Any
    _set_future_exception: Any
    _clear_mate_tracking: Any
    _reset_current_info: Any
    _set_state: Any
    trigger_isready: Any
    _resolve_ready_timeout: Any
    stop: Any

    @staticmethod
    def _abandon_future(future: asyncio.Future[TFutureResult] | None) -> None:
        if future is None:
            return
        if not future.done():
            future.cancel()
            return
        if not future.cancelled():
            _ = future.exception()

    async def _ensure_started(self) -> None:
        if not self._is_started:
            raise RuntimeError("AsyncUsiEngine not started; use 'async with' or call start()")

    async def _send_position(self, sfen: str, moves: Sequence[Move] | None) -> None:
        if sfen.startswith("position "):
            command = sfen
        elif sfen == "startpos":
            command = "position startpos"
        else:
            command = f"position sfen {sfen}"
        if moves:
            command += f" moves {' '.join(m.to_usi() for m in moves)}"
        await self._send_command(command)

    async def _sync_ignored_bestmoves_before_go(self) -> None:
        if self._ignored_bestmove_count <= 0:
            return
        sync_timeout = min(self._handshake_timeout, _STALE_BESTMOVE_SYNC_TIMEOUT_SECONDS)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[%s] synchronizing before go to drain %d stale bestmove marker(s)",
                self.name,
                self._ignored_bestmove_count,
            )
        await self.trigger_isready(timeout=sync_timeout)
        if self._ignored_bestmove_count > 0:
            logger.warning(
                "[%s] clearing %d stale bestmove marker(s) after isready sync",
                self.name,
                self._ignored_bestmove_count,
            )
            self._ignored_bestmove_count = 0

    def _recover_from_stop_timeout(self, future: asyncio.Future[UsiThinkResult]) -> None:
        self._ignored_bestmove_count += 1
        self._set_future_exception(future, RuntimeError("Search aborted because stop timed out"))
        if self._bestmove_future is future:
            self._bestmove_future = None
        self._info_handler = None
        self._reset_current_info()
        self._clear_ponder_handle()
        if self._state in {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.PONDER,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
        }:
            self._set_state(UsiEngineState.READY, reason="stop timed out")

    def _recover_mate_from_stop_timeout(self, future: asyncio.Future[UsiMateResult]) -> None:
        self._set_future_exception(future, RuntimeError("Mate search aborted because stop timed out"))
        if self._mate_future is future:
            self._mate_future = None
        self._clear_mate_tracking()
        self._info_handler = None
        self._reset_current_info()
        if self._state == UsiEngineState.WAITING_FOR_CHECKMATE:
            self._set_state(UsiEngineState.READY, reason="mate stop timed out")

    def _set_state(self, new_state: UsiEngineState, *, reason: str | None = None) -> None:
        if self._state == new_state:
            return
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[%s] state %s -> %s%s",
                self.name,
                self._state.value,
                new_state.value,
                f" ({reason})" if reason else "",
            )
        self._state = new_state

    def _ensure_state(self, allowed: set[UsiEngineState]) -> None:
        if self._state not in allowed:
            allowed_states = ", ".join(state.value for state in sorted(allowed, key=lambda s: s.value))
            raise RuntimeError(
                f"Engine '{self.name}' is in state {self._state.value}; expected one of: {allowed_states}"
            )

    def _ensure_ready_future(self) -> asyncio.Future[None]:
        if self._readyok_future is not None and not self._readyok_future.done():
            return self._readyok_future
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._readyok_future = future
        return future

    async def _wait_for_thinking_to_finish(self, *, timeout: ReadyTimeout = _READY_TIMEOUT_DEFAULT) -> None:
        timeout_value = self._resolve_ready_timeout(timeout)
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        deadline = None if timeout_value is None else start_time + timeout_value
        while True:
            wait_target: asyncio.Future[UsiThinkResult] | asyncio.Future[UsiMateResult] | None = None
            if self._bestmove_future is not None and not self._bestmove_future.done():
                wait_target = self._bestmove_future
            elif self._mate_future is not None and not self._mate_future.done():
                wait_target = self._mate_future
            if wait_target is None:
                return
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                wait_slice = min(remaining, 1.0)
            else:
                wait_slice = 1.0
            try:
                await asyncio.wait_for(asyncio.shield(wait_target), timeout=wait_slice)
            except TimeoutError:
                if deadline is None:
                    continue
        raise TimeoutError("Timed out waiting for ongoing search to finish before isready")

    async def _ponder_hit(
        self,
        request_id: int,
        timings: PonderHitTimings | None,
        timeout: float | None,
    ) -> UsiThinkResult:
        if self._ponder_handle is None or self._ponder_request_id != request_id:
            raise RuntimeError("No active ponder session for ponderhit")
        handle = self._ponder_handle
        if handle.requires_timings and timings is None:
            raise ValueError("timings must be provided when early ponder is enabled")
        future = self._bestmove_future
        if future is None:
            raise RuntimeError("No pending bestmove future for ponderhit")
        command = "ponderhit"
        if handle.requires_timings and timings is not None:
            command += timings.to_command_suffix()
        self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="sending ponderhit")
        await self._send_command(command)
        try:
            # Keep bestmove future pending on timeout so caller can recover via cancel_ponder().
            result = await asyncio.wait_for(asyncio.shield(future), timeout or self._handshake_timeout)
            return result
        finally:
            if future.done():
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                self._clear_ponder_handle()

    async def _cancel_ponder(self, request_id: int, timeout: float | None) -> UsiThinkResult | None:
        if self._ponder_handle is None or self._ponder_request_id != request_id:
            return None
        try:
            return await self.stop(timeout=timeout)
        finally:
            self._clear_ponder_handle()

    def _clear_ponder_handle(self) -> None:
        if self._ponder_handle is not None:
            self._ponder_handle._is_active = False
        self._ponder_handle = None

    @staticmethod
    def _is_process_send_unavailable_error(exc: RuntimeError, *, command: str) -> bool:
        message = str(exc)
        if command == "stop":
            if "broken pipe" in message.lower() and command in message:
                return True
            if (
                "cannot send" in message
                and "stop" in message
                and ("not running" in message.lower() or "stdin closed" in message.lower())
            ):
                return True
        return "is not running; cannot send" in message and f"'{command}'" in message

    async def _stop_without_wait(self) -> bool:
        if self._last_sent_command == "stop":
            return False
        if not self._process.is_running():
            logger.debug("[%s] skipping stop because process is not running", self.name)
            return False
        try:
            await self._send_command("stop")
        except RuntimeError as exc:
            if self._is_process_send_unavailable_error(exc, command="stop"):
                logger.debug("[%s] skipping stop because process exited before stop could be sent", self.name)
                return False
            raise
        if self._state == UsiEngineState.PONDER:
            self._set_state(UsiEngineState.WAITING_FOR_PONDER_BESTMOVE, reason="stop sent during ponder")
        return True
