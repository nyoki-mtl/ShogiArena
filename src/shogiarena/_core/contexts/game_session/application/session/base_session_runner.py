"""Shared session runner base for tournament/SPSA runners."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, cast, runtime_checkable

from shogiarena._core.contexts.game_session.application.progress.reporters import NullProgressReporter
from shogiarena._core.contexts.game_session.application.session.context_holder import SessionContextHolder
from shogiarena._core.contexts.game_session.application.session.flow import SessionFlow
from shogiarena._core.contexts.game_session.application.session.run_controller import RunController
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
from shogiarena._core.shared.kernel.session_hooks import GameLifecycleHooks, SessionStopController
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots

logger = logging.getLogger(__name__)


TFinal = TypeVar("TFinal", covariant=True)
TRun = TypeVar("TRun")


@runtime_checkable
class _OrchestratorPort(OrchestratorPort[Any], Protocol):
    def get_engine_option_snapshots(self) -> EngineOptionsSnapshots: ...

    def get_engine_info_snapshots(self) -> EngineInfoSnapshots: ...


class _DashboardAssetLifecyclePort(Protocol):
    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None: ...

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None: ...


class _DashboardCoordinatorPort(Protocol):
    async def start_server(self, run_dir: Path, preferred_port: int, num_workers: int) -> int: ...

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

    async def start_server(self, _run_dir: Path, preferred_port: int, _num_workers: int) -> int:
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
        self._run_controller = RunController(
            attach_orchestrator=self.attach_orchestrator,
            detach_orchestrator=self._detach_orchestrator,
            stop_services=self.stop_services,
        )
        self._session_flow = SessionFlow(cast(SessionRunnerPort[TRun], self))
        self._result_store = result_store
        type(self)._active_dashboard_manager = self._dashboard_manager

    _active_dashboard_manager: _DashboardAssetLifecyclePort = _NoopDashboardAssetLifecycle()
    dashboard_profiles: tuple[DashboardProfile, ...] = ("tournament", "spsa", "match", "sprt")

    @staticmethod
    def _coerce_instance_pool(pool: Any) -> _InstancePoolBootstrapPort:
        if isinstance(pool, _InstancePoolBootstrapPort):
            return pool
        raise TypeError("instance_pool must expose ensure_local_instance()")

    # --- Dashboard server lifecycle --------------------------------------
    async def start_dashboard_server(self, run_dir: Path, preferred_port: int, num_workers: int) -> int:
        """Start the dashboard API server with port fallback."""
        port = await self._dashboard.start_server(run_dir, preferred_port, num_workers)
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
        await self.stop_dashboard_server()
        run_dir = self._resolve_dashboard_run_dir()
        if run_dir is not None:
            try:
                self.cleanup_dashboard_assets(run_dir)
            except (OSError, RuntimeError) as exc:
                logger.warning("Failed to clean dashboard assets in %s: %s", run_dir, exc)
        await self._stop_additional_services()
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
    async def run_orchestrator(self, orchestrator: _OrchestratorPort, run_coro: Awaitable[TRun]) -> TRun | None:
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
            return cast(TFinal | None, await self._session_flow.run())
        finally:
            self._progress = previous

    # --- Hook methods to be implemented/overridden ----------------------
    async def prepare_run_dir(self) -> None:  # pragma: no cover - to be implemented
        raise NotImplementedError

    async def prepare_domain(self) -> None:  # optional
        return

    async def init_services(self) -> None:  # pragma: no cover - to be implemented
        raise NotImplementedError

    def get_dashboard_params(self) -> tuple[Path, int, int] | None:  # (run_dir, port, num_workers)
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


__all__ = [
    "BaseSessionRunner",
]
