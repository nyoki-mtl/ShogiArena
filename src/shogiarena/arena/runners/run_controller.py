"""Run control helpers for session runners."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import TypeVar

from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator

logger = logging.getLogger(__name__)

TRun = TypeVar("TRun")

_GRACEFUL_SHUTDOWN_WARN_SECONDS = 5.0


class RunController:
    """Coordinate orchestrator execution and shutdown behavior."""

    def __init__(
        self,
        *,
        attach_orchestrator: Callable[[BaseOrchestrator], None],
        detach_orchestrator: Callable[[], None],
        stop_services: Callable[[], Awaitable[None]],
    ) -> None:
        self._attach_orchestrator = attach_orchestrator
        self._detach_orchestrator = detach_orchestrator
        self._stop_services = stop_services

    async def run_orchestrator(self, orchestrator: BaseOrchestrator, run_coro: Awaitable[TRun]) -> TRun | None:
        """Attach and run an orchestrator with cooperative shutdown."""
        self._attach_orchestrator(orchestrator)

        loop = asyncio.get_running_loop()
        current = asyncio.current_task()
        shutdown_task: asyncio.Task[object] | None = None
        sigint_count = 0
        warn_task: asyncio.Task[None] | None = None
        reasons_cache: list[str] | None = None

        def _describe_shutdown_wait() -> list[str]:
            nonlocal reasons_cache
            if reasons_cache is not None:
                return reasons_cache
            reasons: list[str] = []

            active_running = sum(1 for t in orchestrator._running_tasks if not t.done())
            if active_running:
                reasons.append(f"running_tasks={active_running}")

            active_workers = sum(1 for t in orchestrator._worker_tasks if not t.done())
            if active_workers:
                reasons.append(f"worker_tasks={active_workers}")

            progress_task = orchestrator._progress_hub._progress_task
            if progress_task is not None and not progress_task.done():
                reasons.append("progress_hub=active")

            engine_pool = orchestrator.engine_pool
            if engine_pool is not None:
                active_engines = sum(len(v) for v in engine_pool.in_use.values())
                if active_engines:
                    reasons.append(f"engines_in_use={active_engines}")

            reasons_cache = reasons
            return reasons

        def _refresh_shutdown_wait() -> list[str]:
            nonlocal reasons_cache
            reasons_cache = None
            return _describe_shutdown_wait()

        def _on_sigint() -> None:
            nonlocal shutdown_task, sigint_count, warn_task, reasons_cache
            sigint_count += 1
            if sigint_count >= 2:
                logger.warning("Force quitting after second SIGINT")
                os._exit(130)
            reasons = _refresh_shutdown_wait()
            reason_hint = f" ({', '.join(reasons)})" if reasons else ""
            logger.warning("Graceful shutdown requested%s; press Ctrl+C again to force quit", reason_hint)
            orchestrator.request_stop()
            if shutdown_task is None:
                shutdown_task = loop.create_task(orchestrator.shutdown())
            if warn_task is None:

                async def _warn_if_slow() -> None:
                    await asyncio.sleep(_GRACEFUL_SHUTDOWN_WARN_SECONDS)
                    if shutdown_task is not None and not shutdown_task.done():
                        reasons = _refresh_shutdown_wait()
                        reason_hint = f" ({', '.join(reasons)})" if reasons else ""
                        logger.warning(
                            "Graceful shutdown is taking longer than %.1fs%s; waiting for ongoing work to finish",
                            _GRACEFUL_SHUTDOWN_WARN_SECONDS,
                            reason_hint,
                        )

                warn_task = loop.create_task(_warn_if_slow())
            if current is not None:
                current.cancel()

        loop.add_signal_handler(signal.SIGINT, _on_sigint)

        try:
            result = await run_coro
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("Cancelled by user (SIGINT)")
            orchestrator.request_stop()
            await orchestrator.shutdown()
            await self._stop_services()
            self._detach_orchestrator()
            return None
        else:
            await orchestrator.shutdown()
            self._detach_orchestrator()
            return result
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            if warn_task is not None:
                warn_task.cancel()
