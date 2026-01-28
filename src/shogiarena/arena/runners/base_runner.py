from __future__ import annotations

import asyncio
import logging
import typing as t
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from shogiarena.arena.instances.pool import InstancePool
from shogiarena.arena.orchestrators.base_orchestrator import BaseOrchestrator
from shogiarena.arena.session import (
    GameLifecycleHooks,
    LifecycleHooksBase,
    SessionContext,
    SessionStopController,
)
from shogiarena.web.dashboard.backend.assets_writer import DashboardProfile

from .dashboard_manager import DashboardManager
from .run_controller import RunController
from .session_flow import SessionFlow

logger = logging.getLogger(__name__)


def _jsonify(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonify(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str | bytes | bytearray)):
        return [_jsonify(v) for v in value]
    if isinstance(value, set):
        return [_jsonify(v) for v in value]
    return value


def serialize_rules_config(rules: Any) -> dict[str, Any]:
    if rules is None:
        return {}
    if not (is_dataclass(rules) and not isinstance(rules, type)):
        raise TypeError("rules configuration must be a dataclass instance")

    payload = _jsonify(asdict(rules))
    if not isinstance(payload, dict):
        raise TypeError("Failed to serialise rules configuration")

    time_control = getattr(rules, "time_control", None)
    if time_control is not None:
        try:
            payload["time_control_spec"] = time_control.to_spec_str()
        except (RuntimeError, ValueError, TypeError) as exc:  # pragma: no cover - defensive
            logger.warning("Failed to encode time control spec: %s", exc)
    return payload


TFinal = TypeVar("TFinal", covariant=True)
TRun = TypeVar("TRun")


class BaseSessionRunner(Generic[TFinal, TRun]):
    """Shared base for session runners (tournament/SPSA).

    - Prepares dashboard assets
    - Starts/stops API server with port fallback
    - Coordinates orchestrator cooperative shutdown
    - Provides idempotent service stop hooks for subclasses
    """

    def __init__(
        self,
        instance_pool: InstancePool | None = None,
        *,
        dashboard_profiles: Sequence[DashboardProfile] | None = None,
    ) -> None:
        if dashboard_profiles is not None:
            self.dashboard_profiles = tuple(dashboard_profiles)
        self.api_server = None
        self._orchestrator: BaseOrchestrator | None = None
        self._services_closed: bool = False
        # Optional instance pool injected by the CLI entrypoints
        if instance_pool is None:
            instance_pool = InstancePool.load_default_local() or InstancePool()
            instance_pool.ensure_local_instance()
        self.instance_pool: InstancePool | None = instance_pool
        self._session_context: SessionContext | None = None
        self._lifecycle_hooks: GameLifecycleHooks | None = None
        self._stop_controller = SessionStopController()
        self._dashboard_manager = DashboardManager(
            instance_pool=self.instance_pool,
            session_runner=self,
            profiles=self.dashboard_profiles,
        )
        self._run_controller = RunController(
            attach_orchestrator=self.attach_orchestrator,
            detach_orchestrator=self._detach_orchestrator,
            stop_services=self.stop_services,
        )
        self._session_flow = SessionFlow(self)

    dashboard_profiles: tuple[DashboardProfile, ...] = ("tournament", "spsa", "match", "sprt")

    # --- Dashboard assets -------------------------------------------------
    def ensure_dashboard_assets(self, run_dir: Path, num_workers: int) -> None:
        self._dashboard_manager.ensure_assets(run_dir, num_workers)

    # --- Dashboard server lifecycle --------------------------------------
    async def start_dashboard_server(self, run_dir: Path, preferred_port: int, num_workers: int) -> int:
        """Start the dashboard API server with port fallback."""
        port = await self._dashboard_manager.start_server(run_dir, preferred_port, num_workers)
        self.api_server = self._dashboard_manager.api_server
        return port

    async def stop_dashboard_server(self) -> None:
        await self._dashboard_manager.stop_server()
        self.api_server = self._dashboard_manager.api_server

    # --- Orchestrator coordination ---------------------------------------
    def attach_orchestrator(self, orch: BaseOrchestrator) -> None:
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
        if self._services_closed:
            return
        await self.stop_dashboard_server()
        run_dir = self._resolve_dashboard_run_dir()
        if run_dir is not None:
            try:
                self.cleanup_dashboard_assets(run_dir)
            except (OSError, RuntimeError) as exc:
                logger.warning("Failed to clean dashboard assets in %s: %s", run_dir, exc)
        # subclass-specific work
        await self._stop_additional_services()
        self._services_closed = True

    async def _stop_additional_services(self) -> None:  # to be overridden
        return

    # --- Run directory cleanup -------------------------------------------
    @staticmethod
    def cleanup_run_dir(run_dir: Path, *, files: list[str] | None = None, dirs: list[str] | None = None) -> None:
        """Remove known artifacts in run_dir with logging."""
        DashboardManager.cleanup_run_dir(run_dir, files=files, dirs=dirs)

    @staticmethod
    def cleanup_dashboard_assets(run_dir: Path) -> None:
        DashboardManager.cleanup_dashboard_assets(run_dir)

    # --- Orchestrator execution wrapper ----------------------------------
    async def run_orchestrator(self, orchestrator: BaseOrchestrator, run_coro: Awaitable[TRun]) -> TRun | None:
        """Attach and run an orchestrator with cooperative shutdown."""
        return await self._run_controller.run_orchestrator(orchestrator, run_coro)

    # --- Synchronous wrapper ---------------------------------------------
    def run_sync(self) -> TFinal | None:
        """Run the async runner from synchronous code.

        - Installs/uses the same cooperative shutdown semantics as run().
        - Swallows Ctrl+C to provide a clean exit without stack traces.
        - Returns the result of run() or None if cancelled.
        """
        try:
            return asyncio.run(self.run())
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("Cancelled by user (SIGINT)")
            asyncio.run(self.shutdown())
            return None

    # --- Template run flow ------------------------------------------------
    async def run(self) -> TFinal | None:
        """Standard session run flow used by all runners."""
        return await self._session_flow.run()

    # no helper needed; subclasses implement BaseOrchestrator.run()

    # --- Hook methods to be implemented/overridden -----------------------
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

    async def create_orchestrator(
        self,
        hooks: GameLifecycleHooks,
        session_context: SessionContext | None,
    ) -> BaseOrchestrator:  # pragma: no cover - to be implemented
        raise NotImplementedError

    def create_lifecycle_hooks(self, controller: SessionStopController) -> GameLifecycleHooks:
        return LifecycleHooksBase(controller)

    def set_lifecycle_hooks(self, hooks: GameLifecycleHooks) -> None:
        self._lifecycle_hooks = hooks

    @property
    def stop_controller(self) -> SessionStopController:
        return self._stop_controller

    def services_closed(self) -> bool:
        return self._services_closed

    def build_session_context(self) -> SessionContext | None:
        return None

    def set_session_context(self, session_context: SessionContext | None) -> None:
        self._session_context = session_context

    async def run_pre_orchestration_hooks(self, orchestrator: BaseOrchestrator) -> None:  # optional
        return

    def _resolve_dashboard_run_dir(self) -> Path | None:
        if self._session_context is not None:
            ctx_run_dir = getattr(self._session_context, "run_dir", None)
            if ctx_run_dir is not None:
                try:
                    return Path(ctx_run_dir)
                except TypeError:
                    return None
        cfg = getattr(self, "config", None)
        if cfg is not None:
            cfg_run_dir = getattr(cfg, "run_dir", None)
            if cfg_run_dir is not None:
                try:
                    return Path(cfg_run_dir)
                except TypeError:
                    return None
        direct = getattr(self, "run_dir", None)
        if direct is not None:
            try:
                return Path(direct)
            except TypeError:
                return None
        return None

    async def finalize_and_persist(self, run_result: TRun | None) -> TFinal | None:  # optional
        return t.cast(TFinal | None, run_result)
