"""Synchronous wrapper around :class:`AsyncUsiEngine`."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

from rshogi.core import Move

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_engine import (
    AnalysisHandle,
    AsyncUsiEngine,
    PonderHandle,
    PonderHitTimings,
    ReadyTimeout,
    UsiMateResult,
)
from shogiarena.arena.engines.usi_engine import (
    InfoHandler as AsyncInfoHandler,
)
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.engines.usi_types import UsiThinkResult
from shogiarena.arena.instances.pool import InstancePool

T = TypeVar("T")
InfoHandler = AsyncInfoHandler


class SyncAnalysisHandle:
    """Synchronous facade for :class:`AnalysisHandle`."""

    def __init__(self, handle: AnalysisHandle, loop: asyncio.AbstractEventLoop) -> None:
        self._handle = handle
        self._loop = loop

    def stop(self) -> None:
        """Stop the underlying infinite analysis synchronously."""
        fut = asyncio.run_coroutine_threadsafe(self._handle.stop(), self._loop)
        fut.result()


class SyncPonderHandle:
    """Synchronous facade for :class:`PonderHandle`."""

    def __init__(self, handle: PonderHandle, loop: asyncio.AbstractEventLoop) -> None:
        self._handle = handle
        self._loop = loop

    @property
    def active(self) -> bool:
        return self._handle.active

    @property
    def predicted_move(self) -> Move | None:
        return self._handle.predicted_move

    @property
    def requires_timings(self) -> bool:
        return self._handle.requires_timings

    def hit(self, *, timings: PonderHitTimings | None = None, timeout: float | None = None) -> UsiThinkResult:
        fut = asyncio.run_coroutine_threadsafe(self._handle.hit(timings=timings, timeout=timeout), self._loop)
        return fut.result()

    def cancel(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        fut = asyncio.run_coroutine_threadsafe(self._handle.cancel(timeout=timeout), self._loop)
        return fut.result()


class SyncUsiEngine:
    """
    Blocking wrapper over :class:`AsyncUsiEngine`.

    A dedicated event loop is spawned in a background thread so that callers can
    interact with USI engines without touching ``asyncio`` primitives.
    """

    def __init__(
        self,
        engine: AsyncUsiEngine,
        *,
        collect_info_strings: bool = False,
    ) -> None:
        self._engine = engine
        if collect_info_strings:
            self._engine._collect_info_strings = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name=f"sync-usi-{engine.name}", daemon=True)
        self._thread.start()
        self._closed = False
        self._started = False

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #
    @dataclass(slots=True)
    class EngineLaunchSpec:
        path: str | Path
        timeout: float | None = 10.0
        extra_options: dict[str, Any] | None = None
        engine_name: str | None = None
        instance_id: str | None = None
        instance_pool: InstancePool | None = None
        collect_info_strings: bool = False

    @classmethod
    def from_launch_spec(cls, spec: EngineLaunchSpec) -> SyncUsiEngine:
        """Instantiate via :func:`EngineFactory.create_engine` using a spec object."""
        timeout_value = spec.timeout if spec.timeout is not None else 10.0

        return cls.from_config_path(
            spec.path,
            timeout=timeout_value,
            extra_options=spec.extra_options,
            engine_name=spec.engine_name,
            instance_id=spec.instance_id,
            instance_pool=spec.instance_pool,
            collect_info_strings=spec.collect_info_strings,
        )

    @classmethod
    def from_config_path(
        cls,
        path: str | Path,
        *,
        timeout: float = 10.0,
        extra_options: dict[str, Any] | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: InstancePool | None = None,
        collect_info_strings: bool = False,
    ) -> SyncUsiEngine:
        """Instantiate via :func:`EngineFactory.create_engine`."""

        future: concurrent.futures.Future[AsyncUsiEngine] = concurrent.futures.Future()

        def worker() -> None:
            async def _create() -> AsyncUsiEngine:
                return await EngineFactory.create_engine(
                    Path(path),
                    timeout=timeout,
                    extra_options=extra_options,
                    engine_name=engine_name,
                    instance_id=instance_id,
                    instance_pool=instance_pool,
                )

            try:
                engine = asyncio.run(_create())
            except BaseException as exc:  # pragma: no cover - synchronous wrapper
                future.set_exception(exc)
            else:
                future.set_result(engine)

        creator = threading.Thread(target=worker, name="sync-usi-create", daemon=True)
        creator.start()
        engine = future.result()
        return cls(engine, collect_info_strings=collect_info_strings)

    @classmethod
    def from_async_engine(
        cls,
        engine: AsyncUsiEngine,
        *,
        collect_info_strings: bool = False,
    ) -> SyncUsiEngine:
        """Wrap an existing :class:`AsyncUsiEngine`."""
        return cls(engine, collect_info_strings=collect_info_strings)

    # ------------------------------------------------------------------ #
    # Context manager helpers
    # ------------------------------------------------------------------ #
    def __enter__(self) -> SyncUsiEngine:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Core lifecycle operations
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._started:
            return
        self._run_coroutine(self._engine.start())
        self._started = True

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._started:
                self._run_coroutine(self._engine.close())
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()

    # ------------------------------------------------------------------ #
    # Engine command wrappers
    # ------------------------------------------------------------------ #
    def trigger_isready(self, timeout: ReadyTimeout = "default") -> None:
        self._ensure_started()
        self._run_coroutine(self._engine.trigger_isready(timeout=timeout))

    def new_game(self) -> None:
        self._ensure_started()
        self._run_coroutine(self._engine.new_game())

    def submit_position(self, sfen: str, moves: Iterable[str] | None = None) -> None:
        self._ensure_started()
        self._run_coroutine(self._engine.submit_position(sfen, tuple(moves or ())))

    def think(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Iterable[str] | None = None,
        info_handler: InfoHandler | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        self._ensure_started()
        return self._run_coroutine(
            self._engine.think(
                sfen=sfen,
                request=request,
                moves=tuple(moves or ()),
                info_handler=info_handler,
                timeout=timeout,
            )
        )

    def think_mate(
        self,
        *,
        sfen: str,
        ply_limit: int | None = None,
        node_limit: int | None = None,
        infinite: bool = False,
        moves: Iterable[str] | None = None,
        info_handler: InfoHandler | None = None,
        wait_for_bestmove: bool | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult:
        self._ensure_started()
        return self._run_coroutine(
            self._engine.think_mate(
                sfen=sfen,
                ply_limit=ply_limit,
                node_limit=node_limit,
                infinite=infinite,
                moves=tuple(moves or ()),
                info_handler=info_handler,
                wait_for_bestmove=wait_for_bestmove,
                timeout=timeout,
            )
        )

    def analyze(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Iterable[str] | None = None,
        info_handler: InfoHandler | None = None,
    ) -> SyncAnalysisHandle:
        self._ensure_started()
        handle = self._run_coroutine(
            self._engine.analyze(
                sfen=sfen,
                request=request,
                moves=tuple(moves or ()),
                info_handler=info_handler,
            )
        )
        return SyncAnalysisHandle(handle, self._loop)

    def stop(self, timeout: float | None = None) -> UsiThinkResult | None:
        self._ensure_started()
        return self._run_coroutine(self._engine.stop(timeout=timeout))

    def start_ponder(
        self,
        *,
        sfen: str,
        request: UsiThinkRequest,
        moves: Iterable[str] | None = None,
        predicted_move: Move | None = None,
        info_handler: InfoHandler | None = None,
        enable_early_ponder: bool | None = None,
    ) -> SyncPonderHandle:
        self._ensure_started()
        handle = self._run_coroutine(
            self._engine.start_ponder(
                sfen=sfen,
                request=request,
                moves=tuple(moves or ()),
                predicted_move=predicted_move,
                info_handler=info_handler,
                enable_early_ponder=enable_early_ponder,
            )
        )
        return SyncPonderHandle(handle, self._loop)

    def ponder_hit(
        self,
        *,
        timings: PonderHitTimings | None = None,
        timeout: float | None = None,
    ) -> UsiThinkResult:
        self._ensure_started()
        handle = self._engine.active_ponder
        if handle is None:
            raise RuntimeError("No active ponder session for ponderhit")
        return SyncPonderHandle(handle, self._loop).hit(timings=timings, timeout=timeout)

    def cancel_ponder(self, *, timeout: float | None = None) -> UsiThinkResult | None:
        self._ensure_started()
        handle = self._engine.active_ponder
        if handle is None:
            return None
        return SyncPonderHandle(handle, self._loop).cancel(timeout=timeout)

    # ------------------------------------------------------------------ #
    # Data accessors
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self._engine.name

    @property
    def is_running(self) -> bool:
        return self._engine.is_running

    @property
    def is_ready(self) -> bool:
        return self._engine.is_ready

    @property
    def is_thinking(self) -> bool:
        return self._engine.is_thinking

    @property
    def engine_info(self) -> dict[str, str]:
        return dict(self._engine.engine_info)

    def get_usi_options(self) -> dict[str, dict[str, Any]]:
        return self._engine.get_usi_options()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coroutine(self, coro: Coroutine[Any, Any, T]) -> T:
        fut: concurrent.futures.Future[T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("SyncUsiEngine not started; call start() or use as a context manager.")
