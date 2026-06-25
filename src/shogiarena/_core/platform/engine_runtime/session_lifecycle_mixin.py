"""Lifecycle and readiness synchronization helpers for ``AsyncUsiEngine``."""

from __future__ import annotations

import asyncio
import glob
import logging
from pathlib import Path
from typing import Any

from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    _READY_TIMEOUT_DEFAULT,
    ReadyTimeout,
    UsiEngineStartError,
    UsiEngineState,
)

logger = logging.getLogger(__name__)


class AsyncUsiEngineLifecycleMixin:
    _isready_locks: dict[str, asyncio.Lock]

    name: str
    is_thinking: bool
    config: Any
    _process: Any
    _monitor_task: asyncio.Task[None] | None
    _is_started: bool
    _is_closing: bool
    _handshake_timeout: float
    _analysis_handle: Any
    _readyok_future: asyncio.Future[None] | None
    _usiok_future: asyncio.Future[None] | None
    _state: UsiEngineState
    _has_ready_once: bool

    _set_state: Any
    _monitor_output: Any
    _perform_handshake: Any
    _apply_config_options: Any
    _ensure_ready_future: Any
    _maybe_log_handshake_command: Any
    _send_command: Any
    _resolve_ready_timeout: Any
    _wait_for_thinking_to_finish: Any
    _stop_without_wait: Any
    _clear_ponder_handle: Any
    stop: Any
    _emit_debug_log: Any
    _shutdown_io_log_dispatcher: Any
    _emit_lifecycle_event: Any
    process_info: Any

    @property
    def is_running(self) -> bool:
        return self._process.is_running()

    async def start(self) -> None:
        if self._is_started:
            return
        try:
            self._set_state(UsiEngineState.WAITING_FOR_USIOK, reason="starting engine process")
            await self._process.start()
            self._emit_lifecycle_event("process_started")
            self._monitor_task = asyncio.create_task(self._monitor_output(), name=f"usi-monitor-{self.name}")
            await self._perform_handshake()
            await self._apply_config_options()
            await self.trigger_isready()
        except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
            # Capture the failure phase before close() mutates the state machine
            # to QUIT_COMPLETED; otherwise an isready timeout is misreported as
            # an engine_start failure.
            failure_phase = self._startup_failure_phase()
            try:
                await self.close()
            except (TimeoutError, OSError, RuntimeError) as close_exc:
                logger.warning("[%s] failed to close after start error: %s", self.name, close_exc, exc_info=True)
            logger.warning("[%s] start failed: %s", self.name, exc, exc_info=True)
            raise UsiEngineStartError(
                engine_name=self.name,
                engine_path=str(self.config.engine_path) if self.config.engine_path else None,
                reason=exc,
                working_directory=str(self.config.working_directory) if self.config.working_directory else None,
                command=self._startup_command(),
                options=dict(self.config.options),
                failure_phase=failure_phase,
            ) from exc
        self._is_started = True

    def _startup_command(self) -> tuple[str, ...]:
        engine_path = str(self.config.engine_path) if self.config.engine_path else ""
        args = tuple(str(item) for item in getattr(self.config, "engine_args", ()))
        return (engine_path, *args) if engine_path else args

    def _startup_failure_phase(self) -> str:
        if self._state == UsiEngineState.WAITING_FOR_READYOK:
            return "isready"
        return "engine_start"

    async def close(self) -> None:
        if self._is_closing:
            return
        if not self._is_started and self._monitor_task is None and self._state == UsiEngineState.QUIT_COMPLETED:
            return
        process_info = self.process_info
        self._is_closing = True
        try:
            if self._analysis_handle is not None:
                if self.is_running:
                    try:
                        await self._analysis_handle.stop()
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        logger.exception("Error stopping analysis for %s during close: %s", self.name, exc)
                else:
                    logger.debug("Skipping analysis stop for %s during close because process is not running", self.name)
                    self._analysis_handle = None
            try:
                if self.is_running:
                    await self._stop_without_wait()
                else:
                    logger.debug("Skipping ponder stop for %s during close because process is not running", self.name)
                self._clear_ponder_handle()
            except (TimeoutError, OSError, RuntimeError) as exc:
                logger.exception("Error stopping ponder for %s during close: %s", self.name, exc)
            self._set_state(UsiEngineState.WILL_QUIT, reason="closing engine")
            try:
                await self._process.stop()
            except RuntimeError as exc:
                if "not running" not in str(exc):
                    raise
            if self._monitor_task:
                if not self._monitor_task.done():
                    self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                except (OSError, RuntimeError) as exc:
                    logger.exception("Monitor task error while closing %s: %s", self.name, exc)
            self._monitor_task = None
            await self._shutdown_io_log_dispatcher()
        finally:
            self._is_started = False
            self._is_closing = False
            self._set_state(UsiEngineState.QUIT_COMPLETED, reason="engine closed")
            self._emit_lifecycle_event("process_exited", process_info=process_info)

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
        if self.config.should_skip_isready_lock_if_exists:
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
        except (TypeError, ValueError) as exc:
            logger.debug("[%s] Invalid isready lock key %r: %s", self.name, key, exc)
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
        except OSError as exc:
            logger.debug("[%s] Failed to inspect isready lock path %s: %s", self.name, candidate, exc)
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
        sync_strategy = str(self.config.isready_sync_strategy or "direct").strip().lower()
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
            except (TimeoutError, OSError, RuntimeError) as exc:
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
            except TimeoutError:
                now = loop.time()
                if now - last_notice >= 2.0:
                    last_notice = now
                if deadline is None:
                    continue
        self._readyok_future = None
        raise TimeoutError("USI handshake timed out waiting for readyok")

    async def _perform_handshake(self) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._usiok_future = future
        previous_state = self._state
        self._set_state(UsiEngineState.WAITING_FOR_USIOK, reason="sent usi")
        try:
            self._maybe_log_handshake_command("usi")
            await self._send_command("usi")
        except (TimeoutError, OSError, RuntimeError) as exc:
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
                # Shield the future so the per-slice timeout does not cancel it (matching the
                # readyok path); otherwise a usiok arriving after the first 1s slice is lost and
                # the next wait raises CancelledError, breaking slow-engine startup.
                await asyncio.wait_for(asyncio.shield(future), timeout=min(remaining, 1.0))
                self._usiok_future = None
                return
            except TimeoutError:
                now = loop.time()
                if now - last_notice >= 2.0:
                    last_notice = now
        self._usiok_future = None
        raise TimeoutError("USI handshake timed out waiting for usiok")
