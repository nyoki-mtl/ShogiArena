"""Shared session runner base for tournament/SPSA runners."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, cast, runtime_checkable

from shogiarena._core.contexts.game_session.application.progress.reporters import NullProgressReporter
from shogiarena._core.contexts.game_session.application.session.context_holder import SessionContextHolder
from shogiarena._core.contexts.game_session.application.session.flow import SessionFlow
from shogiarena._core.contexts.game_session.application.session.run_controller import RunController
from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.contexts.game_session.ports.result_store import ResultStorePort
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import (
    DashboardProfile,
    OrchestratorPort,
    ProgressReporterPort,
    RunOptions,
    SessionRunnerPort,
)
from shogiarena._core.contexts.game_session.ports.session_runner_ports import DashboardServerPort
from shogiarena._core.shared.kernel.runtime_watchdog import LoopLagProbePort, RuntimeWatchdog
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks, SessionStopController
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots

logger = logging.getLogger(__name__)


TFinal = TypeVar("TFinal", covariant=True)
TRun = TypeVar("TRun")


@runtime_checkable
class _OrchestratorPort(OrchestratorPort[Any], Protocol):
    def get_engine_option_snapshots(self) -> EngineOptionsSnapshots: ...

    def get_engine_info_snapshots(self) -> EngineInfoSnapshots: ...


@runtime_checkable
class _WatchdogAwareOrchestrator(Protocol):
    def set_runtime_watchdog(self, probe: LoopLagProbePort | None) -> None: ...


class _DashboardAssetLifecyclePort(Protocol):
    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None: ...

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None: ...


class _DashboardCoordinatorPort(Protocol):
    async def start_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int: ...

    async def stop_server(self) -> None: ...

    @property
    def api_server(self) -> Any | None: ...


@runtime_checkable
class _InstancePoolBootstrapPort(Protocol):
    def ensure_local_instance(self) -> object: ...


@runtime_checkable
class _RunnerRunDirPort(Protocol):
    run_dir: Path | str | None


class _NoopDashboardAssetLifecycle:
    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None:
        del run_dir, files, dirs

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None:
        del run_dir


class _NoopDashboardCoordinator:
    api_server: Any = None

    async def start_server(self, _run_dir: Path, _host: str, preferred_port: int, _num_workers: int) -> int:
        return preferred_port

    async def stop_server(self) -> None:
        return None


class BaseSessionRunner(ABC, Generic[TFinal, TRun]):
    """Shared base for session runners (tournament/SPSA).

    - Prepares dashboard assets
    - Starts/stops API server with port fallback
    - Coordinates orchestrator cooperative shutdown
    - Provides idempotent service stop hooks for subclasses
    """

    def __init__(
        self,
        instance_pool: Any | None = None,
        *,
        storage: RunStoragePort,
        instance_pool_factory: Callable[[], Any] | None = None,
        run_options: RunOptions | None = None,
        progress_reporter: ProgressReporterPort | None = None,
        dashboard_profiles: Sequence[DashboardProfile] | None = None,
        dashboard_manager: _DashboardAssetLifecyclePort | None = None,
        dashboard_coordinator: _DashboardCoordinatorPort | None = None,
        result_store: ResultStorePort | None = None,
    ) -> None:
        if dashboard_profiles is not None:
            self.dashboard_profiles = tuple(dashboard_profiles)
        self.api_server: DashboardServerPort | None = None
        self._orchestrator: _OrchestratorPort | None = None
        self._has_closed_services: bool = False
        if instance_pool is None:
            if instance_pool_factory is None:
                raise RuntimeError("instance_pool or instance_pool_factory is required")
            instance_pool = instance_pool_factory()
            if instance_pool is None:
                raise RuntimeError("instance_pool_factory returned None")
            bootstrap_pool = self._coerce_instance_pool(instance_pool)
            bootstrap_pool.ensure_local_instance()
        self.instance_pool: Any | None = instance_pool
        self._storage = storage
        self._run_options = run_options or RunOptions()
        self._progress = progress_reporter or NullProgressReporter()
        self.session_phase: str = "starting"
        self.pending_reschedule: object | None = None
        self.should_stop_when_idle: bool = False
        self._session_manager = SessionContextHolder()
        self._dashboard_manager = dashboard_manager or _NoopDashboardAssetLifecycle()
        self._dashboard = dashboard_coordinator or _NoopDashboardCoordinator()
        self._run_controller: RunController[TRun] = RunController(
            attach_orchestrator=self.attach_orchestrator,
            detach_orchestrator=self._detach_orchestrator,
            stop_services=self.stop_services,
        )
        self._session_flow = SessionFlow(cast(SessionRunnerPort[TRun], self))
        self._result_store = result_store
        # Production stall watchdog for the run's event loop (task 0047, log-only for now).
        self._watchdog: RuntimeWatchdog | None = None
        # Write to the base class because the static cleanup helpers read
        # BaseSessionRunner._active_dashboard_manager; writing to type(self) (the concrete
        # subclass) left those reads on the no-op default, so asset cleanup never ran. (One
        # active manager per process; runs are sequential.)
        BaseSessionRunner._active_dashboard_manager = self._dashboard_manager

    _active_dashboard_manager: _DashboardAssetLifecyclePort = _NoopDashboardAssetLifecycle()
    dashboard_profiles: tuple[DashboardProfile, ...] = ("tournament", "spsa", "match", "sprt")

    @staticmethod
    def _coerce_instance_pool(pool: Any) -> _InstancePoolBootstrapPort:
        if isinstance(pool, _InstancePoolBootstrapPort):
            return pool
        raise TypeError("instance_pool must expose ensure_local_instance()")

    # --- Dashboard server lifecycle --------------------------------------
    async def start_dashboard_server(self, run_dir: Path, host: str, preferred_port: int, num_workers: int) -> int:
        """Start the dashboard API server with port fallback."""
        port = await self._dashboard.start_server(run_dir, host, preferred_port, num_workers)
        self.api_server = self._dashboard.api_server
        return port

    async def stop_dashboard_server(self) -> None:
        await self._dashboard.stop_server()
        self.api_server = self._dashboard.api_server

    # --- Orchestrator coordination ---------------------------------------
    def attach_orchestrator(self, orch: OrchestratorPort[Any]) -> None:
        if not isinstance(orch, _OrchestratorPort):
            raise TypeError("orchestrator must expose engine snapshot accessors")
        self._orchestrator = orch
        # Hand the run's stall watchdog to the orchestrator so timeout attribution can use it (0047).
        if isinstance(orch, _WatchdogAwareOrchestrator):
            orch.set_runtime_watchdog(self._watchdog)

    def _detach_orchestrator(self) -> None:
        self._orchestrator = None

    async def shutdown(self) -> None:
        """Gracefully stop orchestrator and services (idempotent)."""
        orch = self._orchestrator
        if orch is not None:
            orch.request_stop()
            await orch.shutdown()
            self._orchestrator = None
        await self.stop_services()

    # --- Services stop (subclass hook) -----------------------------------
    async def stop_services(self) -> None:
        if self._has_closed_services:
            return
        cleanup_error: Exception | None = None
        try:
            await self.stop_dashboard_server()
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            logger.warning("Failed to stop dashboard server: %s", exc, exc_info=True)
            cleanup_error = exc
        run_dir = self._resolve_dashboard_run_dir()
        if run_dir is not None:
            try:
                self.cleanup_dashboard_assets(run_dir)
            except (OSError, RuntimeError) as exc:
                logger.warning("Failed to clean dashboard assets in %s: %s", run_dir, exc)
                if cleanup_error is None:
                    cleanup_error = exc
        try:
            await self._stop_additional_services()
        except (OSError, RuntimeError, ValueError) as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error
        self._has_closed_services = True

    async def _stop_additional_services(self) -> None:  # to be overridden
        return

    # --- Run directory cleanup -------------------------------------------
    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None:
        """Remove known artifacts in run_dir with logging."""
        BaseSessionRunner._active_dashboard_manager.cleanup_run_dir(run_dir, files=files, dirs=dirs)

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None:
        BaseSessionRunner._active_dashboard_manager.cleanup_dashboard_assets(run_dir)

    # --- Orchestrator execution wrapper ---------------------------------
    async def run_orchestrator(self, orchestrator: OrchestratorPort[Any], run_coro: Awaitable[Any]) -> TRun | None:
        """Attach and run an orchestrator with cooperative shutdown."""
        return await self._run_controller.run_orchestrator(orchestrator, run_coro)

    # --- Template run flow -----------------------------------------------
    async def run(
        self,
        *,
        progress_reporter: ProgressReporterPort | None = None,
    ) -> TFinal | None:
        """Standard session run flow used by all runners."""
        previous = self._progress
        if progress_reporter is not None:
            self._progress = progress_reporter
        try:
            async with self.runtime_watchdog_session():
                return cast(TFinal | None, await self._session_flow.run())
        finally:
            self._progress = previous

    @asynccontextmanager
    async def runtime_watchdog_session(self) -> AsyncIterator[None]:
        """run の間だけ stall watchdog を動かす（task 0047）。

        ``run`` を上書きするサブクラスも必ずこれで包むこと。包み忘れると watchdog が
        None のままになり、timeout attribution が黙って UNATTRIBUTED に倒れる。
        """

        watchdog = RuntimeWatchdog()
        self._watchdog = watchdog
        watchdog.start()
        try:
            yield
        finally:
            await watchdog.stop()

    # --- Hook methods to be implemented/overridden ----------------------
    async def prepare_run_dir(self) -> None:  # pragma: no cover - to be implemented
        raise NotImplementedError

    async def prepare_domain(self) -> None:  # optional
        return

    async def init_services(self) -> None:  # pragma: no cover - to be implemented
        raise NotImplementedError

    def get_dashboard_params(self) -> tuple[Path, str, int, int] | None:
        return None

    async def seed_initial_summary(self) -> None:  # optional
        return

    @abstractmethod
    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> OrchestratorPort[TRun]: ...

    @abstractmethod
    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks: ...

    def set_lifecycle_hooks(self, hooks: GameLifecycleHooks) -> None:
        self._session_manager.set_hooks(hooks)

    @property
    def stop_controller(self) -> SessionStopController:
        return self._session_manager.stop_controller

    def reset_stop_controller(self, controller: SessionStopController) -> None:
        self._session_manager.reset_stop_controller(controller)

    def are_services_closed(self) -> bool:
        return self._has_closed_services

    def build_session_context(self) -> SessionContext | None:
        return None

    def set_session_context(self, session_context: SessionContext | None) -> None:
        self._session_manager.set_context(session_context)
        if session_context is not None:
            try:
                session_context.save_to_storage()
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Failed to persist session context: %s", exc)

    async def run_pre_orchestration_hooks(self, orchestrator: OrchestratorPort[Any]) -> None:  # optional
        _ = orchestrator
        return

    def _resolve_dashboard_run_dir(self) -> Path | None:
        session_context = self._session_manager.context
        if session_context is not None:
            return session_context.run_dir

        if isinstance(self, _RunnerRunDirPort):
            runner_run_dir = self.run_dir
            if isinstance(runner_run_dir, Path):
                return runner_run_dir
            if isinstance(runner_run_dir, str):
                return Path(runner_run_dir)

        return self._storage.run_dir

    @property
    def storage(self) -> RunStoragePort:
        return self._storage

    @property
    def progress(self) -> ProgressReporterPort:
        return self._progress

    def set_progress(self, reporter: ProgressReporterPort) -> None:
        self._progress = reporter

    async def finalize_and_persist(self, run_result: TRun | None) -> TFinal | None:  # optional
        if run_result is not None:
            raise TypeError("BaseSessionRunner.finalize_and_persist requires subclass override for non-None run_result")
        return None

    def write_interrupted_run_health(self, reason: RunTerminationReason) -> None:  # optional
        """run-health artifact を持つ runner だけが override する（task 0052）。

        SPSA など `completion_status.json` を出力しない session では no-op でよい。
        """
        del reason
        return


__all__ = [
    "BaseSessionRunner",
]
