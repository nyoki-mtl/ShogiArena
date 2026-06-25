"""USI output monitor and line parsing helpers."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from rshogi.core import Move

from shogiarena._core.platform.engine_runtime.usi_engine_session_models import UsiEngineState, UsiMateResult
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiThinkPV, move_from_usi

logger = logging.getLogger(__name__)


class AsyncUsiEngineProtocolMixin:
    _process: Any
    _state: UsiEngineState
    _usiok_future: asyncio.Future[None] | None
    _readyok_future: asyncio.Future[None] | None
    _bestmove_future: asyncio.Future[Any] | None
    _mate_future: asyncio.Future[UsiMateResult] | None
    _pending_mate_result: UsiMateResult | None
    _should_wait_bestmove_after_mate: bool
    _ignored_bestmove_count: int
    _current_aux_info: deque[UsiThinkPV]
    _current_pvs: dict[int, UsiThinkPV]
    _info_handler: Any
    _should_collect_info_strings: bool
    _info_string_log: list[str]
    _analysis_handle: Any
    _options: dict[str, Any]
    engine_info: dict[str, str]
    _has_ready_once: bool
    _parser: Any
    name: str

    _emit_io_log: Any
    _set_state: Any
    _append_handshake_entry: Any
    _handle_id: Any
    _handle_option: Any
    _handle_info: Any
    _handle_bestmove: Any
    _handle_checkmate: Any
    _handle_nomate: Any
    _handle_timeout: Any
    _handle_checkmate_notimplemented: Any
    _collect_sorted_pvs: Any
    _collect_info_strings_snapshot: Any
    _reset_current_info: Any
    _clear_mate_tracking: Any
    _clear_ponder_handle: Any
    _set_future_exception: Any
    _emit_lifecycle_event: Any

    async def _monitor_output(self) -> None:
        try:
            async for raw_line in self._process.receive_lines():
                line = raw_line.strip()
                if not line:
                    continue
                await self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Monitor loop error for %s: %s", self.name, exc)
            self._fail_pending(exc)
            raise
        finally:
            self._fail_pending(RuntimeError(f"USI engine {self.name} output stream ended"))

    async def _handle_line(self, line: str) -> None:
        log_state: str | None = None
        normalized = line.strip()
        if normalized.startswith("usiok"):
            log_state = UsiEngineState.WAITING_FOR_USIOK.value
        elif normalized.startswith("readyok"):
            log_state = UsiEngineState.READY.value
        elif normalized.startswith("id ") and self._state == UsiEngineState.WAITING_FOR_USIOK:
            log_state = UsiEngineState.WAITING_FOR_USIOK.value
        elif normalized.startswith("option ") and self._state == UsiEngineState.WAITING_FOR_USIOK:
            log_state = UsiEngineState.WAITING_FOR_USIOK.value
        elif normalized.startswith("usi"):
            log_state = UsiEngineState.WAITING_FOR_USIOK.value
        self._emit_io_log("in", line, state=log_state)
        if normalized.startswith("usiok"):
            if self._usiok_future and not self._usiok_future.done():
                self._usiok_future.set_result(None)
            if self._state != UsiEngineState.WAITING_FOR_USIOK:
                logger.warning("[%s] received 'usiok' while in state %s", self.name, self._state.value)
                return
            self._set_state(UsiEngineState.NOT_READY, reason="received usiok")
            self._append_handshake_entry("in", line, state=UsiEngineState.WAITING_FOR_USIOK.value)
            self._emit_lifecycle_event("usiok")
            return
        if normalized.startswith("readyok"):
            ready_future = self._readyok_future
            is_waiting_future = False
            if ready_future is not None and not ready_future.done():
                is_waiting_future = True
                ready_future.set_result(None)
            if self._state != UsiEngineState.WAITING_FOR_READYOK:
                if self._state == UsiEngineState.WAITING_FOR_CHECKMATE and not is_waiting_future:
                    logger.debug("[%s] ignoring stray readyok during mate search", self.name)
                    return
                logger.warning("[%s] received 'readyok' while in state %s", self.name, self._state.value)
                if not is_waiting_future:
                    return
            self._set_state(UsiEngineState.READY, reason="received readyok")
            self._has_ready_once = True
            self._append_handshake_entry("in", line, state=UsiEngineState.READY.value)
            self._emit_lifecycle_event("readyok")
            return
        if line.startswith("id "):
            if self._state == UsiEngineState.WAITING_FOR_USIOK:
                self._append_handshake_entry("in", line, state=UsiEngineState.WAITING_FOR_USIOK.value)
            self._handle_id(line)
            return
        if line.startswith("option "):
            if self._state == UsiEngineState.WAITING_FOR_USIOK:
                self._append_handshake_entry("in", line, state=UsiEngineState.WAITING_FOR_USIOK.value)
            self._handle_option(line)
            return
        if line.startswith("info "):
            await self._handle_info(line)
            return
        if line.startswith("bestmove"):
            self._handle_bestmove(line)
            return
        if line.startswith("checkmate"):
            tokens = line.split()
            if len(tokens) >= 2:
                suffix = tokens[1].lower()
                if suffix == "nomate":
                    self._handle_nomate()
                    return
                if suffix == "timeout":
                    self._handle_timeout()
                    return
                if suffix == "notimplemented":
                    self._handle_checkmate_notimplemented()
                    return
            self._handle_checkmate(line)
            return
        if line.startswith("nomate"):
            self._handle_nomate()
            return
        if line.startswith("timeout"):
            self._handle_timeout()
            return

        lowered = line.lower()
        if lowered.startswith("error"):
            raise RuntimeError(f"Engine {self.name} reported error line: {line}")

        logger.warning("[%s] ignoring unhandled USI line: %s", self.name, line)

    def _handle_id(self, line: str) -> None:
        try:
            parsed = self._parser.parse_id(line)
        except ValueError as exc:
            logger.debug("Ignoring invalid id line from %s: %s (%s)", self.name, line, exc)
            return
        if parsed is not None:
            self.engine_info[parsed.key] = parsed.value

    def _handle_option(self, line: str) -> None:
        try:
            parsed = self._parser.parse_option(line)
        except ValueError as exc:
            logger.debug("Ignoring invalid option line from %s: %s (%s)", self.name, line, exc)
            return
        if parsed is not None:
            self._options[parsed.name] = parsed

    async def _handle_info(self, line: str) -> None:
        try:
            pv = self._parser.parse_info(line)
        except ValueError as exc:
            logger.debug("Ignoring invalid info line from %s: %s (%s)", self.name, line, exc)
            return
        if pv is None:
            return
        is_string_only = (
            pv.string is not None
            and pv.multipv is None
            and pv.depth is None
            and pv.seldepth is None
            and pv.nodes is None
            and pv.time is None
            and pv.nps is None
            and pv.hashfull is None
            and pv.eval is None
            and (pv.pv is None or len(pv.pv) == 0)
        )
        if is_string_only:
            self._current_aux_info.append(pv)
            if self._should_collect_info_strings and pv.string is not None:
                self._info_string_log.append(pv.string)
        else:
            multipv_idx = pv.multipv if pv.multipv is not None else 1
            self._current_pvs[multipv_idx] = pv
        handler = self._info_handler
        if handler is None:
            return
        maybe_coro = handler(pv)
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro

    def _handle_bestmove(self, line: str) -> None:
        sorted_pvs = self._collect_sorted_pvs()
        info_strings = self._collect_info_strings_snapshot()
        try:
            result = self._parser.parse_bestmove(line, pvs=sorted_pvs)
        except ValueError as exc:
            logger.debug("Ignoring invalid bestmove line from %s: %s (%s)", self.name, line, exc)
            return
        if result is None:
            return
        result.info_strings = info_strings
        if self._ignored_bestmove_count > 0:
            self._ignored_bestmove_count -= 1
            has_pending_bestmove = self._bestmove_future is not None and not self._bestmove_future.done()
            if not has_pending_bestmove:
                self._reset_current_info()
                self._info_handler = None
                if self._state == UsiEngineState.WAITING_FOR_PONDER_BESTMOVE:
                    self._set_state(UsiEngineState.READY, reason="ignored stale bestmove")
                self._clear_ponder_handle()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("[%s] ignoring stale bestmove: %s", self.name, line)
            return
        if self._state == UsiEngineState.WAITING_FOR_CHECKMATE:
            if self._mate_future and not self._mate_future.done():
                if self._pending_mate_result is not None:
                    self._mate_future.set_result(self._pending_mate_result)
                else:
                    self._mate_future.set_result(
                        UsiMateResult(
                            is_mate=False,
                            moves=(),
                            mate_in_ply=None,
                            pvs=tuple(sorted_pvs),
                            info_strings=info_strings,
                        )
                    )
            self._reset_current_info()
            self._info_handler = None
            self._clear_mate_tracking()
            self._set_state(UsiEngineState.READY, reason="received bestmove during mate search")
            self._clear_ponder_handle()
            return
        if self._state == UsiEngineState.PONDER:
            logger.debug("[%s] dropping early ponder bestmove before ponderhit/stop: %s", self.name, line)
            if self._bestmove_future is not None and not self._bestmove_future.done():
                self._set_future_exception(
                    self._bestmove_future,
                    RuntimeError("Ponder bestmove arrived before ponderhit/stop"),
                )
            self._bestmove_future = None
            self._reset_current_info()
            self._info_handler = None
            self._set_state(UsiEngineState.READY, reason="dropped early ponder bestmove")
            self._clear_ponder_handle()
            return
        expected_states = {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
        }
        if self._state not in expected_states:
            if self._state == UsiEngineState.WAITING_FOR_READYOK:
                logger.debug("[%s] dropping stale bestmove while waiting for readyok: %s", self.name, line)
            else:
                logger.warning("[%s] dropping unexpected 'bestmove' while in state %s", self.name, self._state.value)
            self._reset_current_info()
            self._info_handler = None
            self._clear_ponder_handle()
            return
        if self._bestmove_future and not self._bestmove_future.done():
            self._bestmove_future.set_result(result)
        self._reset_current_info()
        self._info_handler = None
        self._set_state(UsiEngineState.READY, reason="received bestmove")
        self._clear_ponder_handle()

    def _handle_checkmate(self, line: str) -> None:
        parsed_moves: list[Move] = []
        for raw in line.split()[1:]:
            try:
                parsed_moves.append(move_from_usi(raw))
            except ValueError:
                break
        moves = tuple(parsed_moves)
        result = UsiMateResult(
            is_mate=True,
            moves=moves,
            mate_in_ply=len(moves) if moves else None,
            pvs=self._collect_sorted_pvs(),
            info_strings=self._collect_info_strings_snapshot(),
        )
        waiting_mate_future = self._mate_future is not None and not self._mate_future.done()
        if self._mate_future and not self._mate_future.done():
            if self._should_wait_bestmove_after_mate:
                self._pending_mate_result = result
            else:
                self._mate_future.set_result(result)
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'checkmate' while in state %s", self.name, self._state.value)
        if (
            self._should_wait_bestmove_after_mate
            and waiting_mate_future
            and self._state == UsiEngineState.WAITING_FOR_CHECKMATE
        ):
            return
        self._clear_mate_tracking()
        self._set_state(UsiEngineState.READY, reason="received checkmate")

    def _handle_nomate(self) -> None:
        result = UsiMateResult(
            is_mate=False,
            moves=(),
            pvs=self._collect_sorted_pvs(),
            info_strings=self._collect_info_strings_snapshot(),
        )
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_result(result)
        self._clear_mate_tracking()
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'nomate' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received nomate")

    def _handle_timeout(self) -> None:
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_result(
                UsiMateResult(
                    is_mate=False,
                    moves=(),
                    mate_in_ply=None,
                    pvs=self._collect_sorted_pvs(),
                    info_strings=self._collect_info_strings_snapshot(),
                )
            )
        self._clear_mate_tracking()
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'timeout' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received timeout")

    def _handle_checkmate_notimplemented(self) -> None:
        if self._mate_future and not self._mate_future.done():
            self._set_future_exception(self._mate_future, RuntimeError("Engine reported checkmate notimplemented"))
        self._clear_mate_tracking()
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'checkmate notimplemented' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received checkmate notimplemented")

    def _fail_pending(self, exc: Exception) -> None:
        self._set_future_exception(self._bestmove_future, exc)
        self._set_future_exception(self._mate_future, exc)
        self._clear_mate_tracking()
        if self._analysis_handle is not None:
            self._analysis_handle = None
        self._set_future_exception(self._readyok_future, exc)
        self._set_future_exception(self._usiok_future, exc)
        self._reset_current_info()
        self._info_handler = None
        if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
            self._set_state(UsiEngineState.NOT_READY, reason="fail pending")
        self._clear_ponder_handle()
