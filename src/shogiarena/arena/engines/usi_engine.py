"""High-level asynchronous USI engine session implementation."""

import asyncio
import glob
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from rshogi.core import Move

from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol
from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_process import AsyncUsiProcess
from shogiarena.arena.engines.usi_protocol import UsiOption, UsiProtocolParser
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult, move_from_usi

logger = logging.getLogger(__name__)

_HANDSHAKE_COMMANDS = {"usi", "isready", "usinewgame", "setoption"}
_HANDSHAKE_RESPONSES = {"usiok", "readyok"}
_HANDSHAKE_LOG_LIMIT = 200
_STALE_BESTMOVE_SYNC_TIMEOUT_SECONDS = 1.0
_READY_TIMEOUT_DEFAULT: Literal["default"] = "default"
ReadyTimeout = float | None | Literal["default"]


@dataclass(slots=True)
class UsiMateResult:
    """Result of a USI ``go mate`` search."""

    is_mate: bool
    moves: tuple[Move, ...] = ()
    mate_in_ply: int | None = None
    pvs: tuple[UsiThinkPV, ...] = ()
    info_strings: tuple[str, ...] = ()

    def get_last_pv(self, multipv_index: int = 1) -> UsiThinkPV | None:
        for pv in reversed(self.pvs):
            if pv.multipv == multipv_index or (pv.multipv is None and multipv_index == 1):
                return pv
        return None


class AnalysisHandle:
    """Handle for an ongoing ``go infinite`` analysis."""

    def __init__(self, engine: "AsyncUsiEngine", request_id: int) -> None:
        self._engine = engine
        self._request_id = request_id
        self._stopped = False

    async def stop(self) -> None:
        if self._stopped:
            return
        await self._engine._stop_analysis(self._request_id)
        self._stopped = True


InfoHandler = Callable[[UsiThinkPV], Awaitable[None] | None]


@dataclass(slots=True)
class PonderHitTimings:
    """Timings used when issuing ``ponderhit`` (btime/wtime/byoyomi/binc/winc)."""

    btime: int | None = None
    wtime: int | None = None
    byoyomi: int | None = None
    binc: int | None = None
    winc: int | None = None

    def to_command_suffix(self) -> str:
        parts: list[str] = []

        def append(name: str, value: int | None) -> None:
            if value is None:
                return
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            parts.extend([name, str(int(value))])

        append("btime", self.btime)
        append("wtime", self.wtime)
        append("binc", self.binc)
        append("winc", self.winc)
        append("byoyomi", self.byoyomi)

        return "" if not parts else " " + " ".join(parts)


class PonderHandle:
    """Handle for a pending ``go ponder`` request."""

    def __init__(
        self,
        engine: "AsyncUsiEngine",
        request_id: int,
        predicted_move: Move | None,
        *,
        require_timings: bool = False,
    ) -> None:
        self._engine = engine
        self._request_id = request_id
        self._active = True
        self.predicted_move = predicted_move
        self._require_timings = require_timings

    @property
    def active(self) -> bool:
        return self._active

    @property
    def requires_timings(self) -> bool:
        return self._require_timings

    async def hit(
        self,
        *,
        timings: PonderHitTimings | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        if not self._active:
            raise RuntimeError("Ponder handle is no longer active")
        if self._require_timings and timings is None:
            raise ValueError("timings must be provided when early ponder is enabled")
        result = await self._engine._ponder_hit(self._request_id, timings, timeout)
        self._active = False
        return result

    async def cancel(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        if not self._active:
            return None
        result = await self._engine._cancel_ponder(self._request_id, timeout)
        self._active = False
        return result


class UsiEngineState(Enum):
    WAITING_FOR_USIOK = "waiting_for_usiok"
    NOT_READY = "not_ready"
    WAITING_FOR_READYOK = "waiting_for_readyok"
    READY = "ready"
    WAITING_FOR_BESTMOVE = "waiting_for_bestmove"
    PONDER = "ponder"
    WAITING_FOR_PONDER_BESTMOVE = "waiting_for_ponder_bestmove"
    WAITING_FOR_CHECKMATE = "waiting_for_checkmate"
    WILL_QUIT = "will_quit"
    QUIT_COMPLETED = "quit_completed"


_HANDSHAKE_COMMAND_STATE: dict[str, str] = {
    "usi": UsiEngineState.WAITING_FOR_USIOK.value,
    "isready": UsiEngineState.WAITING_FOR_READYOK.value,
    "usinewgame": UsiEngineState.READY.value,
    "setoption": UsiEngineState.WAITING_FOR_USIOK.value,
    "go": UsiEngineState.WAITING_FOR_BESTMOVE.value,
}


class UsiEngineStartError(RuntimeError):
    """Raised when a USI engine fails to start cleanly."""

    def __init__(self, *, engine_name: str, engine_path: str | None, reason: BaseException) -> None:
        detail = f"{reason.__class__.__name__}: {reason}"
        path_hint = f" ({engine_path})" if engine_path else ""
        super().__init__(f"Failed to start USI engine '{engine_name}'{path_hint}: {detail}")
        self.engine_name = engine_name
        self.engine_path = engine_path
        self.reason = reason


class AsyncUsiEngine:
    """High-level USI engine session built atop ``AsyncUsiProcess``."""

    DEFAULT_HANDSHAKE_TIMEOUT = 120.0
    _isready_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        *,
        config: UsiEngineConfig,
        bridge: AsyncUSIProcessBridgeProtocol,
        parser: UsiProtocolParser | None = None,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
        monitor_queue_limit: int = 16,
        collect_info_strings: bool = False,
    ) -> None:
        self.config = config
        self._bridge = bridge
        self._process = AsyncUsiProcess(bridge)
        self._parser = parser or UsiProtocolParser()
        self._handshake_timeout = handshake_timeout
        self._monitor_queue_limit = monitor_queue_limit

        self.engine_info: dict[str, str] = {}
        self._options: dict[str, UsiOption] = {}
        self._handshake_log: deque[dict[str, Any]] = deque(maxlen=_HANDSHAKE_LOG_LIMIT)
        self._handshake_log_handlers: list[Callable[[dict[str, Any]], Awaitable[None] | None]] = []
        self._io_log_handlers: list[Callable[[dict[str, Any]], Awaitable[None] | None]] = []

        self._monitor_task: asyncio.Task[None] | None = None
        self._usiok_future: asyncio.Future[None] | None = None
        self._readyok_future: asyncio.Future[None] | None = None
        self._bestmove_future: asyncio.Future[UsiThinkResult] | None = None
        self._mate_future: asyncio.Future[UsiMateResult] | None = None
        self._pending_mate_result: UsiMateResult | None = None
        self._wait_bestmove_after_mate = False
        self._analysis_handle: AnalysisHandle | None = None
        self._analysis_request_id = 0

        self._ponder_handle: PonderHandle | None = None
        self._ponder_request_id = 0
        self._ignored_bestmove_count = 0

        self._current_pvs: dict[int, UsiThinkPV] = {}
        self._current_aux_info: deque[UsiThinkPV] = deque(maxlen=monitor_queue_limit)
        self._info_handler: InfoHandler | None = None

        self._collect_info_strings = collect_info_strings
        self._info_string_log: list[str] = []

        self._started = False
        self._closing = False
        self._thinking_lock = asyncio.Lock()
        self._state = UsiEngineState.WAITING_FOR_USIOK
        self._has_ready_once = False
        self._last_sent_command: str | None = None

        stderr_hook = getattr(self._bridge, "set_stderr_handler", None)
        if callable(stderr_hook):

            def _stderr_handler(line: str) -> None:
                state: str | None = None
                if self._state in {UsiEngineState.WAITING_FOR_USIOK, UsiEngineState.WAITING_FOR_READYOK}:
                    state = self._state.value
                self._emit_io_log("in", f"[stderr] {line}", state=state)

            stderr_hook(_stderr_handler)

    @property
    def state(self) -> UsiEngineState:
        return self._state

    @property
    def active_ponder(self) -> PonderHandle | None:
        handle = self._ponder_handle
        if handle is None or not handle.active:
            return None
        return handle

    def _reset_current_info(self) -> None:
        self._current_pvs.clear()
        self._current_aux_info.clear()
        self._info_string_log.clear()

    def _collect_info_strings_snapshot(self) -> tuple[str, ...]:
        """現在の ``_info_string_log`` のスナップショットを返す。"""
        if not self._collect_info_strings or not self._info_string_log:
            return ()
        return tuple(self._info_string_log)

    def _collect_sorted_pvs(self) -> tuple[UsiThinkPV, ...]:
        return tuple(self._current_pvs[idx] for idx in sorted(self._current_pvs))

    def _clear_mate_tracking(self) -> None:
        self._pending_mate_result = None
        self._wait_bestmove_after_mate = False

    @staticmethod
    def _abandon_future(future: asyncio.Future[Any] | None) -> None:
        if future is None:
            return
        if not future.done():
            future.cancel()
            return
        if not future.cancelled():
            _ = future.exception()

    @staticmethod
    def _consume_future_exception(future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            return
        try:
            _ = future.exception()
        except (RuntimeError, ValueError):
            return

    def _set_future_exception(self, future: asyncio.Future[Any] | None, exc: Exception) -> None:
        if future is None or future.done():
            return
        future.add_done_callback(self._consume_future_exception)
        future.set_exception(exc)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_running(self) -> bool:
        return self._process.is_running()

    @property
    def is_ready(self) -> bool:
        return self._state == UsiEngineState.READY

    @property
    def is_thinking(self) -> bool:
        return self._state in {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.PONDER,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
            UsiEngineState.WAITING_FOR_CHECKMATE,
        }

    def get_usi_options(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "type": option.option_type,
                "default": option.default,
                "current": option.current,
                "min": option.minimum,
                "max": option.maximum,
                "var": list(option.choices),
            }
            for name, option in self._options.items()
        }

    async def __aenter__(self) -> "AsyncUsiEngine":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def register_handshake_log_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Register a callback invoked for each handshake log entry."""

        self._handshake_log_handlers.append(handler)

        def _remove() -> None:
            try:
                self._handshake_log_handlers.remove(handler)
            except ValueError:
                pass

        return _remove

    def register_io_log_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Register a callback invoked for each engine I/O log entry."""

        self._io_log_handlers.append(handler)

        def _remove() -> None:
            try:
                self._io_log_handlers.remove(handler)
            except ValueError:
                pass

        return _remove

    def _append_handshake_entry(
        self,
        direction: str,
        line: str | None = None,
        *,
        state: str | None = None,
    ) -> None:
        ts = int(time.time() * 1000)
        entry: dict[str, Any] = {"dir": direction, "ts": ts}
        if line:
            entry["line"] = line
        if not state:
            state = self._state.value
        entry["state"] = state
        self._handshake_log.append(entry)
        for handler in list(self._handshake_log_handlers):
            try:
                result = handler(entry)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.debug("[%s] handshake log handler failed", self.name, exc_info=True)

    def _maybe_log_handshake_command(self, command: str) -> None:
        if not command:
            return
        verb = command.strip().split()[0].lower()
        if verb in _HANDSHAKE_COMMANDS or verb == "go":
            state = _HANDSHAKE_COMMAND_STATE.get(verb)
            self._append_handshake_entry("out", command.strip(), state=state)

    def _emit_io_log(self, direction: str, line: str | None, *, state: str | None = None) -> None:
        if not line:
            return
        entry: dict[str, Any] = {
            "dir": direction,
            "line": line,
            "ts": int(time.time() * 1000),
        }
        if not state:
            state = self._state.value
        entry["state"] = state
        for handler in list(self._io_log_handlers):
            try:
                result = handler(entry)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                logger.debug("[%s] io log handler failed", self.name, exc_info=True)

    def _emit_debug_log(self, message: str, *, state: str | None = None) -> None:
        """Emit a non-USI debug line into the engine I/O stream."""
        if not message:
            return
        self._emit_io_log("out", f"[debug] {message}", state=state)

    async def _send_command(self, command: str, *, state: str | None = None) -> None:
        if not command:
            return
        stripped = command.strip()
        if not stripped:
            return
        self._last_sent_command = stripped
        log_state = state
        if log_state is None:
            verb = stripped.split()[0].lower()
            log_state = _HANDSHAKE_COMMAND_STATE.get(verb)
        self._emit_io_log("out", stripped, state=log_state)
        await self._process.send_line(command)

    async def start(self) -> None:
        if self._started:
            return
        try:
            self._set_state(UsiEngineState.WAITING_FOR_USIOK, reason="starting engine process")
            await self._process.start()
            self._monitor_task = asyncio.create_task(self._monitor_output(), name=f"usi-monitor-{self.name}")
            await self._perform_handshake()
            await self._apply_config_options()
            await self.trigger_isready()
        except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as exc:
            try:
                await self.close()
            except (OSError, RuntimeError, asyncio.TimeoutError) as close_exc:
                logger.warning("[%s] failed to close after start error: %s", self.name, close_exc, exc_info=True)
            logger.warning("[%s] start failed: %s", self.name, exc, exc_info=True)
            raise UsiEngineStartError(
                engine_name=self.name,
                engine_path=str(self.config.engine_path) if self.config.engine_path else None,
                reason=exc,
            ) from exc
        self._started = True

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self._analysis_handle is not None:
                await self._analysis_handle.stop()
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            logger.exception("Error stopping analysis for %s during close: %s", self.name, exc)
        try:
            await self._stop_without_wait()
            self._clear_ponder_handle()
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            logger.exception("Error stopping ponder for %s during close: %s", self.name, exc)
        self._set_state(UsiEngineState.WILL_QUIT, reason="closing engine")
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except (OSError, RuntimeError) as exc:
                logger.exception("Monitor task error while closing %s: %s", self.name, exc)
        self._monitor_task = None
        try:
            await self._process.stop()
        finally:
            self._started = False
            self._closing = False
            self._set_state(UsiEngineState.QUIT_COMPLETED, reason="engine closed")

    async def trigger_isready(self, timeout: ReadyTimeout = _READY_TIMEOUT_DEFAULT) -> None:
        lock = self._get_isready_lock()
        if lock is None:
            await self._trigger_isready_internal(timeout)
            return
        if lock.locked():
            self._emit_debug_log("isready: waiting for lock", state=UsiEngineState.WAITING_FOR_READYOK.value)
        async with lock:
            await self._trigger_isready_internal(timeout)

    def _get_isready_lock(self) -> asyncio.Lock | None:
        if self._has_ready_once:
            return None
        key = self.config.isready_lock_key
        if not key or not key.strip():
            return None
        raw_key = key.strip()
        if self.config.isready_lock_skip_if_exists:
            check_keys = self._collect_isready_check_keys(fallback=raw_key)
            if any(self._isready_lock_exists(value) for value in check_keys):
                return None
        normalized = raw_key
        lock = self._isready_locks.get(normalized)
        if lock is None:
            lock = asyncio.Lock()
            self._isready_locks[normalized] = lock
        return lock

    def _isready_lock_exists(self, key: str) -> bool:
        try:
            candidate = Path(key.strip())
        except (TypeError, ValueError):
            return False
        wildcard = any(ch in candidate.as_posix() for ch in ("*", "?", "["))
        if not candidate.is_absolute():
            wd = self.config.working_directory
            if wd and wd.strip():
                candidate = Path(wd) / candidate
        try:
            if wildcard:
                return bool(glob.glob(str(candidate)))
            return candidate.exists()
        except OSError:
            return False

    def _collect_isready_check_keys(self, *, fallback: str) -> list[str]:
        keys: list[str] = []
        for item in self.config.isready_lock_check_templates:
            if item.strip():
                keys.append(item.strip())
        check_key = self.config.isready_lock_check_key
        if check_key and check_key.strip():
            keys.append(check_key.strip())
        if not keys:
            keys.append(str(fallback))
        return keys

    def _resolve_ready_timeout(self, timeout: ReadyTimeout) -> float | None:
        if timeout == _READY_TIMEOUT_DEFAULT:
            return self._handshake_timeout
        if timeout is None:
            return None
        if isinstance(timeout, bool):
            raise TypeError("timeout must be a positive float, None, or 'default'")
        try:
            parsed = float(timeout)
        except (TypeError, ValueError) as exc:
            raise TypeError("timeout must be a positive float, None, or 'default'") from exc
        if parsed <= 0:
            raise ValueError("timeout must be > 0")
        return parsed

    async def _trigger_isready_internal(self, timeout: ReadyTimeout = _READY_TIMEOUT_DEFAULT) -> None:
        if not self.is_running:
            raise RuntimeError("Engine process is not running")
        sync_strategy = str(getattr(self.config, "isready_sync_strategy", "direct") or "direct").strip().lower()
        if sync_strategy not in {"direct", "wait", "stop"}:
            sync_strategy = "direct"
        if self.is_thinking and sync_strategy == "wait":
            await self._wait_for_thinking_to_finish(timeout=timeout)
        elif self.is_thinking and sync_strategy == "stop":
            await self.stop(timeout=self._resolve_ready_timeout(timeout))
        future = self._ensure_ready_future()
        previous_state = self._state
        if self._state not in {
            UsiEngineState.NOT_READY,
            UsiEngineState.READY,
            UsiEngineState.WAITING_FOR_READYOK,
        }:
            logger.warning("[%s] trigger_isready called while in state %s", self.name, self._state.value)
        if self._state != UsiEngineState.WAITING_FOR_READYOK:
            loop = asyncio.get_running_loop()
            new_future: asyncio.Future[None] = loop.create_future()
            self._readyok_future = new_future
            self._set_state(UsiEngineState.WAITING_FOR_READYOK, reason="sent isready")
            try:
                self._maybe_log_handshake_command("isready")
                await self._send_command("isready")
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                self._readyok_future = None
                self._set_state(previous_state, reason="failed to send isready")
                logger.warning("[%s] failed to send isready: %s", self.name, exc, exc_info=True)
                raise
            future = new_future
        timeout_value = self._resolve_ready_timeout(timeout)
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        deadline = None if timeout_value is None else start_time + timeout_value
        last_notice = start_time
        while True:
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                wait_slice = min(remaining, 1.0)
            else:
                wait_slice = 1.0
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=wait_slice)
                self._readyok_future = None
                return
            except asyncio.TimeoutError:
                now = loop.time()
                if now - last_notice >= 2.0:
                    last_notice = now
                if deadline is None:
                    continue
        self._readyok_future = None
        raise asyncio.TimeoutError("USI handshake timed out waiting for readyok")

    async def submit_position(self, sfen: str, moves: Sequence[str] | None = None) -> None:
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
            if self._closing:
                return

            if self._analysis_handle is not None:
                try:
                    await self._analysis_handle.stop()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.exception("Error stopping analysis for %s during gameover: %s", self.name, exc)
                finally:
                    self._analysis_handle = None

            if self._ponder_handle is not None and self._ponder_handle.active:
                try:
                    await self._stop_without_wait()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.exception("Error stopping ponder for %s during gameover: %s", self.name, exc)
                finally:
                    self._clear_ponder_handle()

            if self._mate_future and not self._mate_future.done():
                self._set_future_exception(self._mate_future, RuntimeError("Mate search aborted due to gameover"))
                self._mate_future = None
            self._clear_mate_tracking()

            if self._bestmove_future and not self._bestmove_future.done():
                bestmove_future = self._bestmove_future
                try:
                    await self._stop_without_wait()
                except (OSError, RuntimeError, asyncio.TimeoutError):
                    logger.debug("[%s] failed to send stop during gameover", self.name, exc_info=True)
                finally:
                    self._ignored_bestmove_count += 1
                    self._set_future_exception(bestmove_future, RuntimeError("Search aborted due to gameover"))
                    self._bestmove_future = None

            self._reset_current_info()
            self._info_handler = None

            await self._send_command(f"gameover {normalized}")
            self._set_state(UsiEngineState.NOT_READY, reason=f"gameover {normalized} sent")

    async def think(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Sequence[str] | None = None,
        info_handler: InfoHandler | None = None,
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
                if request.ponder:
                    self._set_state(UsiEngineState.PONDER, reason="sent go ponder")
                else:
                    self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="sent go")
            except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as exc:
                self._abandon_future(future)
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
                    self._set_state(UsiEngineState.READY, reason="go command failed")
                task = asyncio.current_task()
                cancelling = getattr(task, "cancelling", None) if task is not None else None
                cancelling_requested = (
                    callable(cancelling) and cancelling() > 0 or (task is not None and task.cancelled())
                )
                if self._closing or cancelling_requested:
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
        infinite: bool = False,
        moves: Sequence[str] | None = None,
        info_handler: InfoHandler | None = None,
        wait_for_bestmove: bool | None = None,
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
            self._wait_bestmove_after_mate = (
                bool(getattr(self.config, "mate_wait_for_bestmove", False))
                if wait_for_bestmove is None
                else bool(wait_for_bestmove)
            )
            command = self._build_mate_command(
                ply_limit=ply_limit,
                node_limit=node_limit,
                infinite=infinite,
            )
            self._reset_current_info()
            self._info_handler = info_handler
            future = self._mate_future
            try:
                await self._send_position(sfen, moves)
                self._maybe_log_handshake_command(command)
                await self._send_command(command)
                self._set_state(UsiEngineState.WAITING_FOR_CHECKMATE, reason="sent go mate")
            except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as exc:
                self._abandon_future(future)
                self._mate_future = None
                self._clear_mate_tracking()
                self._info_handler = None
                self._reset_current_info()
                if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
                    self._set_state(UsiEngineState.READY, reason="go mate failed")
                task = asyncio.current_task()
                cancelling = getattr(task, "cancelling", None) if task is not None else None
                cancelling_requested = (
                    callable(cancelling) and cancelling() > 0 or (task is not None and task.cancelled())
                )
                if self._closing or cancelling_requested:
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
        infinite: bool,
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
        if infinite and (effective_ply_limit is not None or effective_node_limit is not None):
            raise ValueError("infinite cannot be combined with ply_limit/node_limit")
        if effective_ply_limit is not None and effective_node_limit is not None:
            raise ValueError("Specify only one of ply_limit or node_limit")

        if not infinite and effective_ply_limit is None and effective_node_limit is None:
            default_ply = coerce_positive_int(
                "mate_default_ply_limit", getattr(self.config, "mate_default_ply_limit", None)
            )
            default_nodes = coerce_positive_int(
                "mate_default_node_limit", getattr(self.config, "mate_default_node_limit", None)
            )
            default_infinite = bool(getattr(self.config, "mate_default_infinite", False))
            if default_infinite and (default_ply is not None or default_nodes is not None):
                raise ValueError(
                    "mate_default_infinite must not be combined with mate_default_ply_limit/mate_default_node_limit"
                )
            if default_ply is not None and default_nodes is not None:
                raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
            effective_ply_limit = default_ply
            effective_node_limit = default_nodes
            infinite = default_infinite

        if infinite:
            return "go mate infinite"
        if effective_node_limit is not None:
            return f"go mate nodes {effective_node_limit}"
        if effective_ply_limit is not None:
            return f"go mate {effective_ply_limit}"
        return "go mate"

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[str] | None,
        request: UsiThinkRequest,
        predicted_move: Move | None = None,
        info_handler: InfoHandler | None = None,
        enable_early_ponder: bool | None = None,
    ) -> PonderHandle:
        await self._ensure_started()
        self._ensure_state({UsiEngineState.READY})
        if self._ponder_handle is not None:
            raise RuntimeError("Pondering already active")
        request_with_ponder = request if request.ponder else replace(request, ponder=True)
        sanitized_request = request_with_ponder
        require_timings = False
        if enable_early_ponder is None:
            enable_early_ponder = getattr(self.config, "enable_early_ponder", False)
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
        if enable_early_ponder and request_with_ponder.movetime is None and has_clock_timings:
            sanitized_request = replace(
                request_with_ponder,
                movetime=None,
                btime=None,
                wtime=None,
                binc=None,
                winc=None,
                byoyomi=None,
            )
            require_timings = True
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
                require_timings=require_timings,
            )
            self._ponder_handle = handle
            try:
                await self._send_position(sfen, moves)
                await self._send_command(sanitized_request.to_command())
                self._set_state(UsiEngineState.PONDER, reason="sent go ponder")
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                self._clear_ponder_handle()
                logger.warning("[%s] failed to start ponder: %s", self.name, exc, exc_info=True)
                raise
        return handle

    async def stop(self, timeout: float | None = None) -> UsiThinkResult | None:
        await self._ensure_started()
        lock_acquired = False
        if not self._thinking_lock.locked():
            await self._thinking_lock.acquire()
            lock_acquired = True
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
            if lock_acquired:
                self._thinking_lock.release()

        if think_future is not None:
            timed_out = False
            try:
                result = await asyncio.wait_for(asyncio.shield(think_future), timeout or self._handshake_timeout)
                return result
            except asyncio.TimeoutError:
                timed_out = True
                self._recover_from_stop_timeout(think_future)
                return None
            finally:
                if not timed_out:
                    self._bestmove_future = None
                    self._info_handler = None
                    self._reset_current_info()
                    self._clear_ponder_handle()

        if mate_future is not None:
            timed_out = False
            try:
                await asyncio.wait_for(asyncio.shield(mate_future), timeout or self._handshake_timeout)
            except asyncio.TimeoutError:
                timed_out = True
                self._recover_mate_from_stop_timeout(mate_future)
                return None
            finally:
                if not timed_out and mate_future.done():
                    self._mate_future = None
                    self._clear_mate_tracking()
                    self._info_handler = None
                    self._reset_current_info()
                    if self._state == UsiEngineState.WAITING_FOR_CHECKMATE:
                        self._set_state(UsiEngineState.READY, reason="mate search stopped")
            return None

        return None

    async def analyze(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Sequence[str] | None = None,
        info_handler: InfoHandler | None = None,
    ) -> AnalysisHandle:
        await self._ensure_started()
        if not request.infinite:
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
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
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

    async def _perform_handshake(self) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._usiok_future = future
        previous_state = self._state
        self._set_state(UsiEngineState.WAITING_FOR_USIOK, reason="sent usi")
        try:
            self._maybe_log_handshake_command("usi")
            await self._send_command("usi")
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            self._usiok_future = None
            self._set_state(previous_state, reason="failed to send usi")
            logger.warning("[%s] failed to send usi: %s", self.name, exc, exc_info=True)
            raise
        start_time = loop.time()
        deadline = start_time + self._handshake_timeout
        last_notice = loop.time()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(future, timeout=min(remaining, 1.0))
                self._usiok_future = None
                return
            except asyncio.TimeoutError:
                now = loop.time()
                if now - last_notice >= 2.0:
                    last_notice = now
        self._usiok_future = None
        raise asyncio.TimeoutError("USI handshake timed out waiting for usiok")

    async def _apply_config_options(self) -> None:
        if not self.config.options:
            return
        for name, value in self.config.options.items():
            await self._set_option(name, value)

    async def apply_engine_options(self, options: Mapping[str, Any] | None) -> None:
        """Apply additional engine options on top of the static configuration."""
        if not options:
            return
        if not self._started:
            await self.start()
        for name, value in options.items():
            await self._set_option(name, value)

    async def _set_option(self, name: str, value: Any) -> None:
        candidates = self._normalize_option_candidates(name)
        matched = self._collect_options(candidates)
        if not matched:
            await self._wait_for_option(candidates)
            matched = self._collect_options(candidates)
        if not matched:
            raise RuntimeError(f"Engine '{self.name}' does not expose option '{name}'")
        for option in matched:
            await self._apply_option(option.name, value, option)

    def _collect_options(self, candidates: Sequence[str]) -> list[UsiOption]:
        matched: list[UsiOption] = []
        for candidate in candidates:
            option = self._options.get(candidate)
            if option is None:
                continue
            matched.append(option)
        return matched

    async def _wait_for_option(self, candidates: Sequence[str], timeout: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout if timeout is not None else self._handshake_timeout)
        pending = list(dict.fromkeys(candidates))
        while True:
            if any(candidate in self._options for candidate in pending):
                return
            now = loop.time()
            if now >= deadline:
                break
            await asyncio.sleep(0.01)
        missing = ", ".join(candidate for candidate in pending if candidate not in self._options)
        raise RuntimeError(f"Engine '{self.name}' did not expose options: {missing}")

    @staticmethod
    def _normalize_option_candidates(name: str) -> list[str]:
        tokens = [part.strip() for part in re.split(r"[|,]", name) if part.strip()]
        if not tokens:
            return [name]
        # Preserve order but deduplicate
        seen: set[str] = set()
        ordered: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                ordered.append(token)
        return ordered

    @staticmethod
    def _coerce_check_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int) and value in (0, 1):
            return "true" if value == 1 else "false"
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return "true"
            if normalized in {"false", "no", "off", "0"}:
                return "false"
        raise TypeError(f"check option expects boolean-compatible value; got {value!r}")

    @staticmethod
    def _coerce_spin_value(value: Any, option: UsiOption) -> str:
        try:
            int_value = int(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"spin option expects integer value; got {value!r}") from exc
        if option.minimum is not None and int_value < option.minimum:
            raise ValueError(f"spin option value {int_value} is below minimum {option.minimum}")
        if option.maximum is not None and int_value > option.maximum:
            raise ValueError(f"spin option value {int_value} exceeds maximum {option.maximum}")
        return str(int_value)

    @staticmethod
    def _coerce_combo_value(value: Any, option: UsiOption) -> str:
        text = str(value)
        if option.choices and text not in option.choices:
            raise ValueError(f"combo option value '{text}' must be one of {list(option.choices)}")
        return text

    def _normalize_option_value(self, name: str, value: Any, option: UsiOption) -> str | None:
        opt_type = option.option_type
        if opt_type == "button":
            if value not in (None, "", False):
                raise ValueError(f"button option '{name}' must not include a value")
            return None
        if opt_type == "check":
            return self._coerce_check_value(value)
        if opt_type == "spin":
            return self._coerce_spin_value(value, option)
        if opt_type == "combo":
            return self._coerce_combo_value(value, option)
        if opt_type == "string":
            return "" if value is None else str(value)
        return "" if value is None else str(value)

    async def _apply_option(self, name: str, value: Any, option: UsiOption) -> None:
        cmd_value = self._normalize_option_value(name, value, option)
        command = f"setoption name {name}"
        if cmd_value is not None and cmd_value != "":
            command += f" value {cmd_value}"
        self._maybe_log_handshake_command(command)
        await self._send_command(command)
        if cmd_value is not None:
            option.current = cmd_value

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
            return
        if normalized.startswith("readyok"):
            ready_future = self._readyok_future
            waiting_future = False
            if ready_future is not None and not ready_future.done():
                waiting_future = True
                ready_future.set_result(None)
            if self._state != UsiEngineState.WAITING_FOR_READYOK:
                if self._state == UsiEngineState.WAITING_FOR_CHECKMATE and not waiting_future:
                    logger.debug("[%s] ignoring stray readyok during mate search", self.name)
                    return
                logger.warning("[%s] received 'readyok' while in state %s", self.name, self._state.value)
                if not waiting_future:
                    return
            self._set_state(UsiEngineState.READY, reason="received readyok")
            self._has_ready_once = True
            self._append_handshake_entry("in", line, state=UsiEngineState.READY.value)
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
                    self._handle_nomate(line)
                    return
                if suffix == "timeout":
                    self._handle_timeout(line)
                    return
                if suffix == "notimplemented":
                    self._handle_checkmate_notimplemented()
                    return
            self._handle_checkmate(line)
            return
        if line.startswith("nomate"):
            self._handle_nomate(line)
            return
        if line.startswith("timeout"):
            self._handle_timeout(line)
            return

        lowered = line.lower()
        if lowered.startswith("error"):
            raise RuntimeError(f"Engine {self.name} reported error line: {line}")

        if self._maybe_handle_spsa_param_line(line):
            return

        logger.warning("[%s] ignoring unhandled USI line: %s", self.name, line)

    def _maybe_handle_spsa_param_line(self, line: str) -> bool:
        """Handle legacy YaneuraOu SPSA parameter announcements (CSV-like rows)."""
        if "," not in line:
            return False
        parts = [token.strip() for token in line.split(",")]
        if len(parts) != 6:
            return False
        name = parts[0]
        if not name or " " in name:
            return False
        # Ensure the remaining entries look numeric; tolerate trailing zeros.
        try:
            _ = [float(x) for x in parts[1:]]
        except ValueError:
            return False
        if name not in self._options:
            value = float(parts[1])
            minimum_raw = float(parts[2])
            maximum_raw = float(parts[3])
            _step = float(parts[4])
            _delta = float(parts[5])
            is_integer = all(abs(x - round(x)) < 1e-9 for x in (value, minimum_raw, maximum_raw))
            if is_integer:
                value_str = str(int(round(value)))
                minimum = int(round(minimum_raw))
                maximum = int(round(maximum_raw))
                option_type = "spin"
            else:
                value_str = parts[1]
                minimum = None
                maximum = None
                option_type = "string"
            self._options[name] = UsiOption(
                name=name,
                option_type=option_type,
                default=value_str,
                current=value_str,
                minimum=minimum,
                maximum=maximum,
            )
        return True

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
        pv = self._parser.parse_info(line)
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
            if self._collect_info_strings and pv.string is not None:
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
        result = self._parser.parse_bestmove(line, pvs=sorted_pvs)
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
        expected_states = {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
            UsiEngineState.PONDER,
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
            if self._wait_bestmove_after_mate:
                self._pending_mate_result = result
            else:
                self._mate_future.set_result(result)
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'checkmate' while in state %s", self.name, self._state.value)
        if (
            self._wait_bestmove_after_mate
            and waiting_mate_future
            and self._state == UsiEngineState.WAITING_FOR_CHECKMATE
        ):
            return
        self._clear_mate_tracking()
        self._set_state(UsiEngineState.READY, reason="received checkmate")

    def _handle_nomate(self, line: str) -> None:
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

    def _handle_timeout(self, line: str) -> None:
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

    async def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("AsyncUsiEngine not started; use 'async with' or call start()")

    async def _send_position(self, sfen: str, moves: Sequence[str] | None) -> None:
        if sfen.startswith("position "):
            command = sfen
        elif sfen == "startpos":
            command = "position startpos"
        else:
            command = f"position sfen {sfen}"
        if moves:
            moves_clean = " ".join(move.strip() for move in moves if move.strip())
            if moves_clean:
                command += f" moves {moves_clean}"
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
            wait_target: asyncio.Future[Any] | None = None
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
            except asyncio.TimeoutError:
                if deadline is None:
                    continue
        raise asyncio.TimeoutError("Timed out waiting for ongoing search to finish before isready")

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
        await self._send_command(command)
        self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="sent ponderhit")
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
            self._ponder_handle._active = False
        self._ponder_handle = None

    async def _stop_without_wait(self) -> bool:
        if self._last_sent_command == "stop":
            return False
        await self._send_command("stop")
        if self._state == UsiEngineState.PONDER:
            self._set_state(UsiEngineState.WAITING_FOR_PONDER_BESTMOVE, reason="stop sent during ponder")
        return True
