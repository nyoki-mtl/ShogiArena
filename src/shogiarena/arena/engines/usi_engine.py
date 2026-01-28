"""High-level asynchronous USI engine session implementation."""

import asyncio
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from types import TracebackType
from typing import Any

from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol
from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_process import AsyncUsiProcess
from shogiarena.arena.engines.usi_protocol import UsiOption, UsiProtocolParser
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UsiMateResult:
    """Result of a USI ``go mate`` search."""

    is_mate: bool
    moves: tuple[str, ...] = ()
    mate_in_ply: int | None = None


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
        predicted_move: str | None,
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


class AsyncUsiEngine:
    """High-level USI engine session built atop ``AsyncUsiProcess``."""

    DEFAULT_HANDSHAKE_TIMEOUT = 10.0

    def __init__(
        self,
        *,
        config: UsiEngineConfig,
        bridge: AsyncUSIProcessBridgeProtocol,
        parser: UsiProtocolParser | None = None,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
        monitor_queue_limit: int = 16,
    ) -> None:
        self.config = config
        self._bridge = bridge
        self._process = AsyncUsiProcess(bridge)
        self._parser = parser or UsiProtocolParser()
        self._handshake_timeout = handshake_timeout
        self._monitor_queue_limit = monitor_queue_limit

        self.engine_info: dict[str, str] = {}
        self._options: dict[str, UsiOption] = {}

        self._monitor_task: asyncio.Task[None] | None = None
        self._usiok_future: asyncio.Future[None] | None = None
        self._readyok_future: asyncio.Future[None] | None = None
        self._bestmove_future: asyncio.Future[UsiThinkResult] | None = None
        self._mate_future: asyncio.Future[UsiMateResult] | None = None
        self._analysis_handle: AnalysisHandle | None = None
        self._analysis_request_id = 0

        self._ponder_handle: PonderHandle | None = None
        self._ponder_request_id = 0
        self._ignored_bestmove_count = 0

        self._current_pvs: dict[int, UsiThinkPV] = {}
        self._current_aux_info: deque[UsiThinkPV] = deque(maxlen=monitor_queue_limit)
        self._info_handler: InfoHandler | None = None

        self._started = False
        self._closing = False
        self._thinking_lock = asyncio.Lock()
        self._state = UsiEngineState.WAITING_FOR_USIOK

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

    @staticmethod
    def _abandon_future(future: asyncio.Future[Any] | None) -> None:
        if future is None:
            return
        if not future.done():
            future.cancel()
            return
        if not future.cancelled():
            _ = future.exception()

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_running(self) -> bool:
        return self._process.is_running()

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
            raise
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
            if self._ponder_handle is not None and self._ponder_handle.active:
                await self._ponder_handle.cancel()
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            logger.exception("Error cancelling ponder for %s during close: %s", self.name, exc)
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

    async def trigger_isready(self, timeout: float | None = None) -> None:
        if not self.is_running:
            raise RuntimeError("Engine process is not running")
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
                await self._process.send_line("isready")
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                self._readyok_future = None
                self._set_state(previous_state, reason="failed to send isready")
                logger.warning("[%s] failed to send isready: %s", self.name, exc, exc_info=True)
                raise
            future = new_future
        try:
            await asyncio.wait_for(future, timeout or self._handshake_timeout)
        finally:
            self._readyok_future = None

    async def submit_position(self, sfen: str, moves: Sequence[str] | None = None) -> None:
        await self._ensure_started()
        await self._send_position(sfen, moves)

    async def new_game(self) -> None:
        await self._ensure_started()
        async with self._thinking_lock:
            self._ensure_state({UsiEngineState.READY})
            await self._process.send_line("usinewgame")
            self._set_state(UsiEngineState.NOT_READY, reason="usinewgame sent")
            await self.trigger_isready()
            self._ignored_bestmove_count = 0

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
                    await self._ponder_handle.cancel()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.exception("Error cancelling ponder for %s during gameover: %s", self.name, exc)
                finally:
                    self._ponder_handle = None

            if self._mate_future and not self._mate_future.done():
                self._mate_future.set_exception(RuntimeError("Mate search aborted due to gameover"))
                self._mate_future = None

            if self._bestmove_future and not self._bestmove_future.done():
                try:
                    await self._process.send_line("stop")
                except (OSError, RuntimeError, asyncio.TimeoutError):
                    logger.debug("[%s] failed to send stop during gameover", self.name, exc_info=True)
                self._ignored_bestmove_count += 1

            self._reset_current_info()
            self._info_handler = None

            await self._process.send_line(f"gameover {normalized}")
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
            if self._bestmove_future is not None and not self._bestmove_future.done():
                raise RuntimeError("bestmove already pending")
            loop = asyncio.get_running_loop()
            self._bestmove_future = loop.create_future()
            self._reset_current_info()
            self._info_handler = info_handler
            future = self._bestmove_future
            try:
                await self._send_position(sfen, moves)
                await self._process.send_line(request.to_command())
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
                result = await asyncio.wait_for(future, timeout)
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
        moves: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult:
        await self._ensure_started()
        self._ensure_state({UsiEngineState.READY})
        async with self._thinking_lock:
            if self._mate_future is not None and not self._mate_future.done():
                raise RuntimeError("mate search already pending")
            loop = asyncio.get_running_loop()
            self._mate_future = loop.create_future()
            command = "go mate" if ply_limit is None else f"go mate {int(ply_limit)}"
            try:
                await self._send_position(sfen, moves)
                await self._process.send_line(command)
                self._set_state(UsiEngineState.WAITING_FOR_CHECKMATE, reason="sent go mate")
            except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as exc:
                future = self._mate_future
                self._abandon_future(future)
                self._mate_future = None
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
            try:
                result = await asyncio.wait_for(self._mate_future, timeout)
                return result
            finally:
                self._mate_future = None
                if self._state != UsiEngineState.WAITING_FOR_PONDER_BESTMOVE:
                    self._set_state(UsiEngineState.READY, reason="mate search completed")

    async def start_ponder(
        self,
        *,
        sfen: str,
        moves: Sequence[str] | None,
        request: UsiThinkRequest,
        predicted_move: str | None = None,
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
        if enable_early_ponder:
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
                await self._process.send_line(sanitized_request.to_command())
                self._set_state(UsiEngineState.PONDER, reason="sent go ponder")
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                self._bestmove_future = None
                self._info_handler = None
                self._reset_current_info()
                self._ponder_handle = None
                logger.warning("[%s] failed to start ponder: %s", self.name, exc, exc_info=True)
                raise
        return handle

    async def stop(self, timeout: float | None = None) -> UsiThinkResult | None:
        await self._ensure_started()
        lock_acquired = False
        if not self._thinking_lock.locked():
            await self._thinking_lock.acquire()
            lock_acquired = True
        try:
            future = self._bestmove_future
            if future is None or future.done():
                await self._process.send_line("stop")
                if self._state == UsiEngineState.PONDER:
                    self._set_state(UsiEngineState.WAITING_FOR_PONDER_BESTMOVE, reason="stop sent during ponder")
                return None
            await self._process.send_line("stop")
            if self._state == UsiEngineState.PONDER:
                self._set_state(UsiEngineState.WAITING_FOR_PONDER_BESTMOVE, reason="stop sent during ponder")
        finally:
            if lock_acquired:
                self._thinking_lock.release()

        try:
            result = await asyncio.wait_for(future, timeout or self._handshake_timeout)
            return result
        except asyncio.TimeoutError:
            return None
        finally:
            self._bestmove_future = None
            self._info_handler = None
            self._reset_current_info()
            self._ponder_handle = None

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
            self._analysis_request_id += 1
            handle = AnalysisHandle(self, self._analysis_request_id)
            self._analysis_handle = handle
            self._info_handler = info_handler
            self._reset_current_info()
            try:
                await self._send_position(sfen, moves)
                await self._process.send_line(request.to_command())
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
            await self._process.send_line("stop")
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
            await self._process.send_line("usi")
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            self._usiok_future = None
            self._set_state(previous_state, reason="failed to send usi")
            logger.warning("[%s] failed to send usi: %s", self.name, exc, exc_info=True)
            raise
        try:
            await asyncio.wait_for(future, self._handshake_timeout)
        finally:
            self._usiok_future = None

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

    async def _apply_option(self, name: str, value: Any, option: UsiOption) -> None:
        cmd_value: str | None
        if option.option_type == "button":
            cmd_value = None
        elif isinstance(value, bool):
            cmd_value = "true" if value else "false"
        else:
            cmd_value = str(value)
        command = f"setoption name {name}"
        if cmd_value is not None and cmd_value != "":
            command += f" value {cmd_value}"
        await self._process.send_line(command)
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
        if line == "usiok":
            if self._usiok_future and not self._usiok_future.done():
                self._usiok_future.set_result(None)
            if self._state != UsiEngineState.WAITING_FOR_USIOK:
                logger.warning("[%s] received 'usiok' while in state %s", self.name, self._state.value)
            self._set_state(UsiEngineState.NOT_READY, reason="received usiok")
            return
        if line == "readyok":
            if self._readyok_future and not self._readyok_future.done():
                self._readyok_future.set_result(None)
            if self._state != UsiEngineState.WAITING_FOR_READYOK:
                logger.warning("[%s] received 'readyok' while in state %s", self.name, self._state.value)
            self._set_state(UsiEngineState.READY, reason="received readyok")
            return
        if line.startswith("id "):
            self._handle_id(line)
            return
        if line.startswith("option "):
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
        sorted_pvs = [self._current_pvs[idx] for idx in sorted(self._current_pvs)]
        result = self._parser.parse_bestmove(line, pvs=sorted_pvs)
        if result is None:
            return
        ignore_bestmove = False
        if self._ignored_bestmove_count > 0:
            self._ignored_bestmove_count -= 1
            ignore_bestmove = True
        if self._bestmove_future and not self._bestmove_future.done():
            self._bestmove_future.set_result(result)
        self._reset_current_info()
        self._info_handler = None
        if ignore_bestmove:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("[%s] ignoring bestmove after gameover: %s", self.name, line)
            self._ponder_handle = None
            return
        if self._state not in {
            UsiEngineState.WAITING_FOR_BESTMOVE,
            UsiEngineState.WAITING_FOR_PONDER_BESTMOVE,
            UsiEngineState.PONDER,
        }:
            logger.warning("[%s] received 'bestmove' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received bestmove")
        self._ponder_handle = None

    def _handle_checkmate(self, line: str) -> None:
        moves = tuple(line.split()[1:])
        result = UsiMateResult(is_mate=True, moves=moves, mate_in_ply=len(moves) if moves else None)
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_result(result)
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'checkmate' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received checkmate")

    def _handle_nomate(self, line: str) -> None:
        result = UsiMateResult(is_mate=False, moves=())
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_result(result)
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'nomate' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received nomate")

    def _handle_timeout(self, line: str) -> None:
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_result(UsiMateResult(is_mate=False, moves=(), mate_in_ply=None))
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'timeout' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received timeout")

    def _handle_checkmate_notimplemented(self) -> None:
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_exception(RuntimeError("Engine reported checkmate notimplemented"))
        if self._state != UsiEngineState.WAITING_FOR_CHECKMATE:
            logger.warning("[%s] received 'checkmate notimplemented' while in state %s", self.name, self._state.value)
        self._set_state(UsiEngineState.READY, reason="received checkmate notimplemented")

    def _fail_pending(self, exc: Exception) -> None:
        if self._bestmove_future and not self._bestmove_future.done():
            self._bestmove_future.set_exception(exc)
        if self._mate_future and not self._mate_future.done():
            self._mate_future.set_exception(exc)
        if self._analysis_handle is not None:
            self._analysis_handle = None
        if self._readyok_future and not self._readyok_future.done():
            self._readyok_future.set_exception(exc)
        if self._usiok_future and not self._usiok_future.done():
            self._usiok_future.set_exception(exc)
        self._reset_current_info()
        self._info_handler = None
        if self._state not in {UsiEngineState.WILL_QUIT, UsiEngineState.QUIT_COMPLETED}:
            self._set_state(UsiEngineState.NOT_READY, reason="fail pending")
        self._ponder_handle = None

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
        await self._process.send_line(command)

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
        if timings is not None:
            command += timings.to_command_suffix()
        await self._process.send_line(command)
        self._set_state(UsiEngineState.WAITING_FOR_BESTMOVE, reason="sent ponderhit")
        try:
            result = await asyncio.wait_for(future, timeout or self._handshake_timeout)
            return result
        finally:
            self._bestmove_future = None
            self._info_handler = None
            self._reset_current_info()
            self._ponder_handle = None

    async def _cancel_ponder(self, request_id: int, timeout: float | None) -> UsiThinkResult | None:
        if self._ponder_handle is None or self._ponder_request_id != request_id:
            return None
        try:
            return await self.stop(timeout=timeout)
        finally:
            self._ponder_handle = None
