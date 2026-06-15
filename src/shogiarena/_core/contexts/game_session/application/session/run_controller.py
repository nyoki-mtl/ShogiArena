"""Orchestrator execution coordination with cooperative shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sized
from typing import Protocol, TypeVar, runtime_checkable

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import OrchestratorPort

logger = logging.getLogger(__name__)

TRunFlow = TypeVar("TRunFlow")

_GRACEFUL_SHUTDOWN_WARN_SECONDS = 5.0
_GRACEFUL_SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)


def _resolve_shutdown_hard_timeout() -> float:
    """Hard upper bound for the graceful shutdown wait (env-overridable)."""
    raw = os.getenv("SHOGI_ARENA_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        value = 0.0
    return value if value > 0 else 60.0


_GRACEFUL_SHUTDOWN_HARD_TIMEOUT_SECONDS = _resolve_shutdown_hard_timeout()


def _consume_task_result(task: asyncio.Task[object]) -> None:
    """Retrieve a finished task's outcome so it is not reported as a never-retrieved exception."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("Abandoned graceful shutdown finished with error: %s", exc)


@runtime_checkable
class _EnginePoolUsagePort(Protocol):
    in_use: Mapping[object, Sized]


@runtime_checkable
class _ProgressHubUsagePort(Protocol):
    _progress_task: asyncio.Task[None] | None


@runtime_checkable
class _ShutdownInspectableOrchestratorPort(Protocol):
    _running_tasks: set[asyncio.Task[None]]
    _worker_tasks: set[asyncio.Task[None]]
    _progress_hub: _ProgressHubUsagePort
    engine_pool: object | None


class RunController:
    """Coordinate orchestrator execution and shutdown behavior."""

    def __init__(
        self,
        *,
        attach_orchestrator: Callable[[OrchestratorPort[TRunFlow]], None],
        detach_orchestrator: Callable[[], None],
        stop_services: Callable[[], Awaitable[None]],
    ) -> None:
        self._attach_orchestrator = attach_orchestrator
        self._detach_orchestrator = detach_orchestrator
        self._stop_services = stop_services

    async def run_orchestrator(
        self, orchestrator: OrchestratorPort[TRunFlow], run_coro: Awaitable[TRunFlow]
    ) -> TRunFlow | None:
        """Attach and run an orchestrator with cooperative shutdown."""
        self._attach_orchestrator(orchestrator)

        loop = asyncio.get_running_loop()
        current = asyncio.current_task()
        run_task = asyncio.ensure_future(run_coro)
        shutdown_task: asyncio.Task[None] | None = None
        signal_count = 0
        signal_name: str | None = None
        warn_task: asyncio.Task[None] | None = None
        reasons_cache: list[str] | None = None

        def _describe_shutdown_wait() -> list[str]:
            nonlocal reasons_cache
            if reasons_cache is not None:
                return reasons_cache
            reasons: list[str] = []
            inspectable = orchestrator if isinstance(orchestrator, _ShutdownInspectableOrchestratorPort) else None
            if inspectable is None:
                reasons_cache = reasons
                return reasons

            active_running = sum(1 for task in inspectable._running_tasks if not task.done())
            if active_running:
                reasons.append(f"running_tasks={active_running}")

            active_workers = sum(1 for task in inspectable._worker_tasks if not task.done())
            if active_workers:
                reasons.append(f"worker_tasks={active_workers}")

            progress_task = inspectable._progress_hub._progress_task
            if progress_task is not None and not progress_task.done():
                reasons.append("progress_hub=active")

            engine_pool = inspectable.engine_pool
            if isinstance(engine_pool, _EnginePoolUsagePort):
                active_engines = sum(len(v) for v in engine_pool.in_use.values())
                if active_engines:
                    reasons.append(f"engines_in_use={active_engines}")

            reasons_cache = reasons
            return reasons

        def _refresh_shutdown_wait() -> list[str]:
            nonlocal reasons_cache
            reasons_cache = None
            return _describe_shutdown_wait()

        def _ensure_shutdown_task() -> asyncio.Task[None]:
            nonlocal shutdown_task
            if shutdown_task is None:
                shutdown_task = loop.create_task(orchestrator.shutdown())
            return shutdown_task

        def _schedule_shutdown_warning() -> None:
            nonlocal warn_task
            if warn_task is not None:
                return

            async def _warn_if_slow() -> None:
                await asyncio.sleep(_GRACEFUL_SHUTDOWN_WARN_SECONDS)
                active_shutdown = shutdown_task
                if active_shutdown is not None and not active_shutdown.done():
                    reasons = _refresh_shutdown_wait()
                    reason_hint = f" ({', '.join(reasons)})" if reasons else ""
                    logger.warning(
                        "Graceful shutdown is taking longer than %.1fs%s; waiting for ongoing work to finish",
                        _GRACEFUL_SHUTDOWN_WARN_SECONDS,
                        reason_hint,
                    )

            warn_task = loop.create_task(_warn_if_slow())

        async def _await_shutdown_task() -> None:
            active_shutdown = _ensure_shutdown_task()
            done, _pending = await asyncio.wait({active_shutdown}, timeout=_GRACEFUL_SHUTDOWN_HARD_TIMEOUT_SECONDS)
            if active_shutdown in done:
                # Completed within the bound; await is instant and re-raises any shutdown error.
                await active_shutdown
                return
            # Do NOT await the cancellation: asyncio.wait_for would block until the cancel
            # completes, which an unresponsive shutdown (one that swallows CancelledError) can
            # ignore, re-introducing the hang. Request cancellation, hand off result retrieval to
            # a done callback, and return. Engine processes are force-killed by the pool/bridge
            # kill timeouts regardless.
            logger.error(
                "Graceful shutdown did not complete within %.1fs; abandoning the wait without blocking",
                _GRACEFUL_SHUTDOWN_HARD_TIMEOUT_SECONDS,
            )
            active_shutdown.cancel()
            active_shutdown.add_done_callback(_consume_task_result)

        def _on_shutdown_signal(received_signal: signal.Signals) -> None:
            nonlocal signal_count, signal_name
            signal_count += 1
            signal_name = received_signal.name
            if signal_count >= 2:
                logger.warning("Force quitting after second %s", received_signal.name)
                os._exit(130)
            reasons = _refresh_shutdown_wait()
            reason_hint = f" ({', '.join(reasons)})" if reasons else ""
            logger.warning(
                "Graceful shutdown requested by %s%s; press Ctrl+C again to force quit",
                received_signal.name,
                reason_hint,
            )
            orchestrator.request_stop()
            _ensure_shutdown_task()
            _schedule_shutdown_warning()
            if current is not None:
                current.cancel()

        registered_signals: list[signal.Signals] = []
        for shutdown_signal in _GRACEFUL_SHUTDOWN_SIGNALS:
            try:
                loop.add_signal_handler(shutdown_signal, _on_shutdown_signal, shutdown_signal)
            except (NotImplementedError, ValueError, RuntimeError):
                # Not supported on this platform, or the loop is not on the main thread
                # (add_signal_handler raises ValueError there). Skip rather than abort the run.
                logger.debug("Signal handlers are not available on this event loop: %s", shutdown_signal.name)
                continue
            registered_signals.append(shutdown_signal)

        try:
            result = await run_task
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("Cancelled by user%s", f" ({signal_name})" if signal_name else "")
            orchestrator.request_stop()
            await _await_shutdown_task()
            await self._stop_services()
            self._detach_orchestrator()
            return None
        except Exception:
            # A non-cancellation failure must still release engines/pool/dashboard and detach the
            # orchestrator; otherwise these leak because the finally below only cancels run_task.
            logger.exception("Run failed; shutting down orchestrator")
            orchestrator.request_stop()
            await _await_shutdown_task()
            await self._stop_services()
            self._detach_orchestrator()
            raise
        else:
            await _await_shutdown_task()
            self._detach_orchestrator()
            return result
        finally:
            if not run_task.done():
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
            for shutdown_signal in registered_signals:
                loop.remove_signal_handler(shutdown_signal)
            if warn_task is not None:
                warn_task.cancel()


__all__ = ["RunController"]
