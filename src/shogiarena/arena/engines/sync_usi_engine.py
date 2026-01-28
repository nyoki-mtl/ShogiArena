"""Synchronous wrapper around :class:`AsyncUsiEngine`."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine, Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_engine import (
    AnalysisHandle,
    AsyncUsiEngine,
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


class SyncUsiEngine:
    """
    Blocking wrapper over :class:`AsyncUsiEngine`.

    A dedicated event loop is spawned in a background thread so that callers can
    interact with USI engines without touching ``asyncio`` primitives.
    """

    def __init__(self, engine: AsyncUsiEngine) -> None:
        self._engine = engine
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name=f"sync-usi-{engine.name}", daemon=True)
        self._thread.start()
        self._closed = False
        self._started = False

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #
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
        return cls(engine)

    @classmethod
    def from_async_engine(cls, engine: AsyncUsiEngine) -> SyncUsiEngine:
        """Wrap an existing :class:`AsyncUsiEngine`."""
        return cls(engine)

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
    def trigger_isready(self, timeout: float | None = None) -> None:
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
        moves: Iterable[str] | None = None,
        timeout: float | None = None,
    ) -> UsiMateResult:
        self._ensure_started()
        return self._run_coroutine(
            self._engine.think_mate(
                sfen=sfen,
                ply_limit=ply_limit,
                moves=tuple(moves or ()),
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

    # ------------------------------------------------------------------ #
    # Data accessors
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self._engine.name

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
