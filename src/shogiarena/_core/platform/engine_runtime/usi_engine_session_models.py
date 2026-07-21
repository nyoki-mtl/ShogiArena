"""Shared models and runtime constants for async USI engine sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from rsshogi.core import Move

from shogiarena._core.contexts.match.ports.usi_think_ports import PonderHitTimings, UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_protocol_types import (
    AsyncUsiProcessBridgePort,
    UsiOption,
    UsiThinkPV,
    UsiThinkResult,
    find_last_pv,
)
from shogiarena._core.shared.kernel.engine_io import UsiIoDirection, UsiIoEvent
from shogiarena._core.shared.kernel.engine_process import EngineProcessInfo
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

_HANDSHAKE_COMMANDS = {"usi", "isready", "usinewgame", "setoption"}
_HANDSHAKE_LOG_LIMIT = 200
_STALE_BESTMOVE_SYNC_TIMEOUT_SECONDS = 1.0
_READY_TIMEOUT_DEFAULT: Literal["default"] = "default"
ReadyTimeout = float | None | Literal["default"]
TFutureResult = TypeVar("TFutureResult")
EngineLifecycleEventName = Literal[
    "process_started",
    "usiok",
    "options_applied",
    "readyok",
    "new_game",
    "think_started",
    "think_finished",
    "process_exited",
]
UsiOptionValidationMode = Literal["strict", "warn", "raw", "allow_unlisted_combo_value"]
UsiAnalyzeFailurePolicy = Literal["raise", "collect"]


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineLifecycleEvent:
    """Engine lifecycle callback payload."""

    name: EngineLifecycleEventName
    monotonic_ns: int
    process_info: EngineProcessInfo | None = None
    state: str | None = None
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class UsiAnalyzePosition:
    """Batch analysis target position."""

    sfen: str
    moves: tuple[Move, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class UsiAnalyzeResetPolicy:
    """Per-position reset policy for lightweight analysis capture."""

    new_game: bool = True
    clear_hash_if_available: bool = True
    isready_before_each: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class UsiAnalyzeItem:
    """One result item from batch analysis."""

    index: int
    position: UsiAnalyzePosition
    result: UsiThinkResult | None
    error: Exception | None
    elapsed_ms: int


@runtime_checkable
class _ProcessInfoBridgePort(Protocol):
    def get_process_info(self) -> EngineProcessInfo | None: ...


class AsyncUsiProcess:
    """Minimal lifecycle and I/O wrapper around a ``AsyncUsiProcessBridgePort``."""

    def __init__(self, bridge: AsyncUsiProcessBridgePort) -> None:
        self._bridge = bridge
        self._state_lock = asyncio.Lock()
        self._is_running = False

    @property
    def name(self) -> str:
        return self._bridge.name

    async def start(self) -> None:
        async with self._state_lock:
            if self._is_running:
                raise RuntimeError(f"USI process {self.name} already running")
            await self._bridge.start_process()
            self._is_running = True

    async def stop(self) -> None:
        async with self._state_lock:
            if not self._is_running:
                raise RuntimeError(f"USI process {self.name} not running")
            try:
                await self._bridge.stop_process()
            finally:
                self._is_running = False

    def is_running(self) -> bool:
        return self._is_running and self._bridge.is_running()

    async def send_line(self, command: str) -> None:
        if not self.is_running():
            raise RuntimeError(f"USI process {self.name} is not running; cannot send '{command}'")
        await self._bridge.send_line(command)

    def receive_lines(self) -> AsyncIterator[str]:
        if not self.is_running():
            raise RuntimeError(f"USI process {self.name} is not running; cannot receive output")
        return self._bridge.receive_lines()


@dataclass(slots=True, kw_only=True)
class UsiMateResult:
    """Result of a USI ``go mate`` search."""

    is_mate: bool
    moves: tuple[Move, ...] = ()
    mate_in_ply: int | None = None
    pvs: tuple[UsiThinkPV, ...] = ()
    info_strings: tuple[str, ...] = ()

    def get_last_pv(self, multipv_index: int = 1) -> UsiThinkPV | None:
        return find_last_pv(self.pvs, multipv_index=multipv_index)


class AnalysisHandle:
    """Handle for an ongoing ``go infinite`` analysis."""

    def __init__(self, engine: Any, request_id: int) -> None:
        self._engine = engine
        self._request_id = request_id
        self._is_stopped = False

    async def stop(self) -> None:
        if self._is_stopped:
            return
        await self._engine._stop_analysis(self._request_id)
        self._is_stopped = True


InfoHandlerFn = Callable[[UsiThinkPV], Awaitable[None] | None]
UsiIoHandlerFn = Callable[[UsiIoEvent], Awaitable[None] | None]
EngineLifecycleHandlerFn = Callable[[EngineLifecycleEvent], None]


class UsiEngineSession(Protocol):
    """Public contract for an asynchronous USI engine session."""

    @property
    def name(self) -> str: ...

    @property
    def process_info(self) -> EngineProcessInfo | None: ...

    async def __aenter__(self) -> UsiEngineSession: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def new_game(self) -> None: ...

    async def trigger_isready(self, timeout: ReadyTimeout = _READY_TIMEOUT_DEFAULT) -> None: ...

    async def apply_engine_options(
        self,
        options: Mapping[str, object] | None,
        *,
        clear_hash: bool = True,
        after_setoption: Literal["none", "isready"] = "isready",
        validation: UsiOptionValidationMode | Mapping[str, UsiOptionValidationMode] | None = None,
    ) -> None: ...

    async def think(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Sequence[Move] | None = None,
        info_handler: InfoHandlerFn | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult: ...

    def get_usi_options(self) -> Mapping[str, UsiOption]: ...

    def register_io_log_handler(self, handler: UsiIoHandlerFn) -> Callable[[], None]: ...

    def register_lifecycle_handler(self, handler: EngineLifecycleHandlerFn) -> Callable[[], None]: ...

    def iter_analyze_positions(
        self,
        positions: Sequence[UsiAnalyzePosition],
        *,
        request: UsiThinkRequest,
        timeout_per_position: float | None = None,
        reset_policy: UsiAnalyzeResetPolicy | None = None,
        failure_policy: UsiAnalyzeFailurePolicy = "raise",
    ) -> AsyncIterator[UsiAnalyzeItem]: ...

    async def analyze_positions(
        self,
        positions: Sequence[UsiAnalyzePosition],
        *,
        request: UsiThinkRequest,
        timeout_per_position: float | None = None,
        reset_policy: UsiAnalyzeResetPolicy | None = None,
        failure_policy: UsiAnalyzeFailurePolicy = "raise",
    ) -> tuple[UsiAnalyzeItem, ...]: ...


@runtime_checkable
class _StderrBridgePort(Protocol):
    def set_stderr_handler(self, handler: Callable[[str], None] | None) -> None: ...


class PonderHandle:
    """Handle for a pending ``go ponder`` request."""

    def __init__(
        self,
        engine: Any,
        request_id: int,
        predicted_move: Move | None,
        *,
        should_require_timings: bool = False,
    ) -> None:
        self._engine = engine
        self._request_id = request_id
        self._is_active = True
        self.predicted_move = predicted_move
        self._require_timings = should_require_timings

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def requires_timings(self) -> bool:
        return self._require_timings

    async def hit(
        self,
        *,
        timings: PonderHitTimings | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        if not self._is_active:
            raise RuntimeError("Ponder handle is no longer active")
        if self._require_timings and timings is None:
            raise ValueError("timings must be provided when early ponder is enabled")
        result = await self._engine._ponder_hit(self._request_id, timings, timeout)
        self._is_active = False
        return result

    async def cancel(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        if not self._is_active:
            return None
        result = await self._engine._cancel_ponder(self._request_id, timeout)
        self._is_active = False
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

    def __init__(
        self,
        *,
        engine_name: str,
        engine_path: str | None,
        reason: BaseException,
        working_directory: str | None = None,
        command: tuple[str, ...] = (),
        options: JsonObject | None = None,
        failure_phase: str = "engine_start",
    ) -> None:
        detail = f"{reason.__class__.__name__}: {reason}"
        path_hint = f" ({engine_path})" if engine_path else ""
        super().__init__(f"Failed to start USI engine '{engine_name}'{path_hint}: {detail}")
        self.engine_name = engine_name
        self.engine_path = engine_path
        self.reason = reason
        self.working_directory = working_directory
        self.command = command
        self.options = dict(options or {})
        self.failure_phase = _normalize_start_failure_phase(failure_phase)

    def diagnostic_payload(self) -> JsonObject:
        """Return a JSON-serializable startup diagnostic payload."""

        return {
            "engine": self.engine_name,
            "executable": self.engine_path,
            "working_directory": self.working_directory,
            "command": list(self.command),
            "options": {str(key): json_serialize(value) for key, value in self.options.items()},
            "failure_phase": self.failure_phase,
            "exception_class": type(self.reason).__name__,
            "message": str(self.reason),
        }


def _normalize_start_failure_phase(raw: str) -> Literal["engine_start", "isready"]:
    if raw == "isready":
        return "isready"
    return "engine_start"


__all__ = [
    "AnalysisHandle",
    "AsyncUsiProcess",
    "EngineLifecycleEvent",
    "EngineLifecycleEventName",
    "EngineLifecycleHandlerFn",
    "EngineProcessInfo",
    "InfoHandlerFn",
    "PonderHandle",
    "ReadyTimeout",
    "TFutureResult",
    "UsiAnalyzeFailurePolicy",
    "UsiAnalyzeItem",
    "UsiAnalyzePosition",
    "UsiAnalyzeResetPolicy",
    "UsiEngineStartError",
    "UsiEngineSession",
    "UsiEngineState",
    "UsiIoDirection",
    "UsiIoEvent",
    "UsiIoHandlerFn",
    "UsiMateResult",
    "UsiOptionValidationMode",
    "_HANDSHAKE_COMMANDS",
    "_HANDSHAKE_COMMAND_STATE",
    "_HANDSHAKE_LOG_LIMIT",
    "_ProcessInfoBridgePort",
    "_READY_TIMEOUT_DEFAULT",
    "_STALE_BESTMOVE_SYNC_TIMEOUT_SECONDS",
    "_StderrBridgePort",
]
