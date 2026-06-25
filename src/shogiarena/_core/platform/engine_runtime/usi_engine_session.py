"""High-level asynchronous USI engine session implementation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from types import TracebackType
from typing import Any

from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings
from shogiarena._core.platform.engine_runtime.session_internal_mixin import AsyncUsiEngineInternalMixin
from shogiarena._core.platform.engine_runtime.session_lifecycle_mixin import AsyncUsiEngineLifecycleMixin
from shogiarena._core.platform.engine_runtime.session_options_mixin import AsyncUsiEngineOptionsMixin
from shogiarena._core.platform.engine_runtime.session_ponder_mixin import AsyncUsiEnginePonderMixin
from shogiarena._core.platform.engine_runtime.session_protocol_mixin import AsyncUsiEngineProtocolMixin
from shogiarena._core.platform.engine_runtime.session_search_mixin import AsyncUsiEngineSearchMixin
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    _HANDSHAKE_COMMAND_STATE,
    _HANDSHAKE_COMMANDS,
    _HANDSHAKE_LOG_LIMIT,
    AnalysisHandle,
    AsyncUsiProcess,
    EngineLifecycleEvent,
    EngineLifecycleEventName,
    EngineLifecycleHandlerFn,
    EngineProcessInfo,
    InfoHandlerFn,
    PonderHandle,
    ReadyTimeout,
    TFutureResult,
    UsiEngineStartError,
    UsiEngineState,
    UsiIoDirection,
    UsiIoEvent,
    UsiIoHandlerFn,
    UsiMateResult,
    _ProcessInfoBridgePort,
    _StderrBridgePort,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import (
    AsyncUsiProcessBridgePort,
    UsiOption,
    UsiProtocolParser,
    UsiThinkPV,
)
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class AsyncUsiEngine(
    AsyncUsiEngineProtocolMixin,
    AsyncUsiEnginePonderMixin,
    AsyncUsiEngineSearchMixin,
    AsyncUsiEngineOptionsMixin,
    AsyncUsiEngineLifecycleMixin,
    AsyncUsiEngineInternalMixin,
):
    """High-level USI engine session built atop ``AsyncUsiProcess``."""

    DEFAULT_HANDSHAKE_TIMEOUT = 120.0
    IO_LOG_DRAIN_TIMEOUT_SECONDS = 5.0
    _isready_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        *,
        config: UsiEngineConfig,
        bridge: AsyncUsiProcessBridgePort,
        parser: UsiProtocolParser | None = None,
        handshake_timeout: float | None = None,
        monitor_queue_limit: int = 16,
        should_collect_info_strings: bool | None = None,
        should_collect_raw_io: bool | None = None,
        should_collect_stderr: bool | None = None,
        should_collect_outbound: bool | None = None,
    ) -> None:
        self.config = config
        self._bridge = bridge
        self._process = AsyncUsiProcess(bridge)
        self._parser = parser or UsiProtocolParser()
        if handshake_timeout is not None:
            self._handshake_timeout = handshake_timeout
        elif config.handshake_timeout is not None:
            self._handshake_timeout = config.handshake_timeout
        else:
            self._handshake_timeout = self.DEFAULT_HANDSHAKE_TIMEOUT

        self.engine_info: dict[str, str] = {}
        self._options: dict[str, UsiOption] = {}
        self._handshake_log: deque[JsonObject] = deque(maxlen=_HANDSHAKE_LOG_LIMIT)
        self._io_log_handlers: list[UsiIoHandlerFn] = []
        self._io_log_dispatch_queue: asyncio.Queue[UsiIoEvent | None] | None = None
        self._io_log_dispatch_task: asyncio.Task[None] | None = None
        self._lifecycle_handlers: list[EngineLifecycleHandlerFn] = []

        self._monitor_task: asyncio.Task[None] | None = None
        self._usiok_future: asyncio.Future[None] | None = None
        self._readyok_future: asyncio.Future[None] | None = None
        self._bestmove_future: asyncio.Future[Any] | None = None
        self._mate_future: asyncio.Future[UsiMateResult] | None = None
        self._pending_mate_result: UsiMateResult | None = None
        self._should_wait_bestmove_after_mate = False
        self._analysis_handle: AnalysisHandle | None = None
        self._analysis_request_id = 0

        self._ponder_handle: PonderHandle | None = None
        self._ponder_request_id = 0
        self._ignored_bestmove_count = 0

        self._current_pvs: dict[int, UsiThinkPV] = {}
        self._current_aux_info: deque[UsiThinkPV] = deque(maxlen=monitor_queue_limit)
        self._info_handler: InfoHandlerFn | None = None

        self._should_collect_info_strings = (
            config.should_collect_info_strings if should_collect_info_strings is None else should_collect_info_strings
        )
        self._info_string_log: list[str] = []
        self._should_collect_raw_io = (
            config.should_collect_raw_io if should_collect_raw_io is None else should_collect_raw_io
        )
        self._should_collect_stderr = (
            config.should_collect_stderr if should_collect_stderr is None else should_collect_stderr
        )
        self._should_collect_outbound = (
            config.should_collect_outbound if should_collect_outbound is None else should_collect_outbound
        )

        self._is_started = False
        self._is_closing = False
        self._thinking_lock = asyncio.Lock()
        self._state = UsiEngineState.WAITING_FOR_USIOK
        self._has_ready_once = False
        self._last_sent_command: str | None = None

        if isinstance(self._bridge, _StderrBridgePort):

            def _stderr_handler(line: str) -> None:
                state: str | None = None
                if self._state in {UsiEngineState.WAITING_FOR_USIOK, UsiEngineState.WAITING_FOR_READYOK}:
                    state = self._state.value
                self._emit_io_log("stderr", line, state=state)

            self._bridge.set_stderr_handler(_stderr_handler)

    @property
    def state(self) -> UsiEngineState:
        return self._state

    def _reset_current_info(self) -> None:
        self._current_pvs.clear()
        self._current_aux_info.clear()
        self._info_string_log.clear()

    def _collect_info_strings_snapshot(self) -> tuple[str, ...]:
        """現在の ``_info_string_log`` のスナップショットを返す。"""
        if not self._should_collect_info_strings or not self._info_string_log:
            return ()
        return tuple(self._info_string_log)

    def _collect_sorted_pvs(self) -> tuple[UsiThinkPV, ...]:
        return tuple(self._current_pvs[idx] for idx in sorted(self._current_pvs))

    def _clear_mate_tracking(self) -> None:
        self._pending_mate_result = None
        self._should_wait_bestmove_after_mate = False

    @staticmethod
    def _consume_future_exception(future: asyncio.Future[TFutureResult]) -> None:
        if future.cancelled():
            return
        try:
            _ = future.exception()
        except (RuntimeError, ValueError):
            return

    def _set_future_exception(self, future: asyncio.Future[TFutureResult] | None, exc: Exception) -> None:
        if future is None or future.done():
            return
        future.add_done_callback(self._consume_future_exception)
        future.set_exception(exc)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_thinking(self) -> bool:
        return self._state in {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.PONDER,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
            UsiEngineState.WAITING_FOR_CHECKMATE,
        }

    def get_usi_options(self) -> dict[str, UsiOption]:
        """Return a snapshot of raw USI option declarations."""

        return {name: replace(option) for name, option in self._options.items()}

    @property
    def process_info(self) -> EngineProcessInfo | None:
        if isinstance(self._bridge, _ProcessInfoBridgePort):
            return self._bridge.get_process_info()
        return None

    async def __aenter__(self) -> AsyncUsiEngine:
        await self.start()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        await self.close()

    def register_io_log_handler(
        self,
        handler: UsiIoHandlerFn,
    ) -> Callable[[], None]:
        """Register a callback invoked for each engine I/O log entry."""

        self._io_log_handlers.append(handler)

        def _remove() -> None:
            try:
                self._io_log_handlers.remove(handler)
            except ValueError:
                pass

        return _remove

    def register_lifecycle_handler(self, handler: EngineLifecycleHandlerFn) -> Callable[[], None]:
        """Register a callback invoked for engine lifecycle events."""

        self._lifecycle_handlers.append(handler)

        def _remove() -> None:
            try:
                self._lifecycle_handlers.remove(handler)
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
        entry: JsonObject = {"dir": direction, "ts": ts}
        if line:
            entry["line"] = line
        if not state:
            state = self._state.value
        entry["state"] = state
        self._handshake_log.append(entry)

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
        if direction == "stderr":
            if not self._should_collect_stderr:
                return
        elif not self._should_collect_raw_io:
            return
        if direction == "out" and not self._should_collect_outbound:
            return
        if not state:
            state = self._state.value
        event_direction: UsiIoDirection = "stderr" if direction == "stderr" else "out" if direction == "out" else "in"
        event = UsiIoEvent(
            direction=event_direction,
            line=line,
            monotonic_ns=time.monotonic_ns(),
            phase=state,
            timestamp_ms=int(time.time() * 1000),
        )
        if not self._io_log_handlers:
            return
        self._ensure_io_log_dispatcher()
        queue = self._io_log_dispatch_queue
        if queue is None:
            return
        queue.put_nowait(event)

    def _emit_lifecycle_event(
        self,
        name: EngineLifecycleEventName,
        *,
        details: JsonObject | None = None,
        process_info: EngineProcessInfo | None = None,
    ) -> None:
        if not self._lifecycle_handlers:
            return
        event = EngineLifecycleEvent(
            name=name,
            monotonic_ns=time.monotonic_ns(),
            process_info=self.process_info if process_info is None else process_info,
            state=self._state.value,
            details={} if details is None else details,
        )
        for handler in list(self._lifecycle_handlers):
            try:
                handler(event)
            except Exception:
                logger.warning("[%s] lifecycle handler failed", self.name, exc_info=True)

    def _ensure_io_log_dispatcher(self) -> None:
        task = self._io_log_dispatch_task
        if task is not None and not task.done():
            return
        self._io_log_dispatch_queue = asyncio.Queue()
        self._io_log_dispatch_task = asyncio.create_task(
            self._dispatch_io_logs(),
            name=f"usi-io-log-dispatch-{self.name}",
        )

    async def _dispatch_io_logs(self) -> None:
        queue = self._io_log_dispatch_queue
        if queue is None:
            return
        while True:
            entry = await queue.get()
            try:
                if entry is None:
                    return
                for handler in list(self._io_log_handlers):
                    await self._run_io_log_handler(handler, entry)
            finally:
                queue.task_done()

    async def _run_io_log_handler(
        self,
        handler: UsiIoHandlerFn,
        entry: UsiIoEvent,
    ) -> None:
        try:
            if self._is_async_io_log_handler(handler):
                result = handler(entry)
                if inspect.isawaitable(result):
                    await result
            else:
                # Keep supporting sync wrappers that return an awaitable even if
                # they are not declared with ``async def``.
                result = await asyncio.to_thread(handler, entry)
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[%s] io log handler failed", self.name, exc_info=True)

    @staticmethod
    def _is_async_io_log_handler(handler: UsiIoHandlerFn) -> bool:
        if inspect.iscoroutinefunction(handler):
            return True
        if not callable(handler):
            return False
        return inspect.iscoroutinefunction(handler.__call__)

    async def _shutdown_io_log_dispatcher(self) -> None:
        task = self._io_log_dispatch_task
        queue = self._io_log_dispatch_queue
        if task is None:
            self._io_log_dispatch_queue = None
            return
        if queue is not None:
            queue.put_nowait(None)
        try:
            await asyncio.wait_for(task, timeout=self.IO_LOG_DRAIN_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("[%s] timed out draining io log handlers during shutdown", self.name)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._io_log_dispatch_task = None
            self._io_log_dispatch_queue = None

    async def flush_io_log_handlers(self, *, timeout: float | None = None) -> None:
        """Queued USI I/O ログ handler の処理完了を待つ。"""

        queue = self._io_log_dispatch_queue
        if queue is None:
            return
        timeout_value = self.IO_LOG_DRAIN_TIMEOUT_SECONDS if timeout is None else float(timeout)
        try:
            await asyncio.wait_for(queue.join(), timeout=timeout_value)
        except TimeoutError:
            logger.warning("[%s] timed out flushing io log handlers", self.name)

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
        if "\n" in stripped or "\r" in stripped:
            # A USI command must be a single line. Embedded newlines (e.g. from an unsanitised
            # setoption string value) would split into multiple commands -> reject them.
            raise ValueError("USI command must not contain embedded newline characters")
        self._last_sent_command = stripped
        log_state = state
        if log_state is None:
            verb = stripped.split()[0].lower()
            log_state = _HANDSHAKE_COMMAND_STATE.get(verb)
        self._emit_io_log("out", stripped, state=log_state)
        await self._process.send_line(command)


__all__ = [
    "AnalysisHandle",
    "AsyncUsiEngine",
    "AsyncUsiProcess",
    "EngineLifecycleEvent",
    "EngineProcessInfo",
    "InfoHandlerFn",
    "PonderHandle",
    "PonderHitTimings",
    "ReadyTimeout",
    "UsiIoEvent",
    "UsiEngineStartError",
    "UsiEngineState",
    "UsiMateResult",
]
