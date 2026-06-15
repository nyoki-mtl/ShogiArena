"""Core orchestrator base shared by tournament and SPSA adapters."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

import rshogi.record

from shogiarena._core.contexts.game_session.adapters.engine.pool import EnginePool
from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    compute_max_ply_extra_options,
    create_game_runner_from_rules,
    create_progress_queue,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.application.orchestration.concurrent_executor import (
    run_items_concurrently as _run_items_concurrently_service,
)
from shogiarena._core.contexts.game_session.application.progress.hub import (
    DashboardServerPort,
    ProgressHub,
    SummaryUpdateCallback,
)
from shogiarena._core.contexts.game_session.application.progress.orchestrator_progress_control import (
    preassign_worker as _preassign_worker_service,
)
from shogiarena._core.contexts.game_session.application.progress.orchestrator_progress_control import (
    start_progress_consumer as _start_progress_consumer_service,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import WorkerSnapshotModel
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import EngineLifecyclePolicy
from shogiarena._core.contexts.game_session.ports.session_runner_ports import BeforeGameHookPort
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import compute_pool_capacity
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort
from shogiarena._core.shared.kernel.session_hooks import (
    GameCompletionEvent,
    GameLifecycleHooks,
)
from shogiarena._core.shared.kernel.snapshots import EngineInfoSnapshots, EngineOptionsSnapshots
from shogiarena._core.shared.kernel.time_control import TimeControlLimits

from .config_core import RulesConfig
from .config_engine import EngineConfig

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class BaseOrchestrator:
    """Base class offering convenience methods for orchestrators."""

    engine_configs: dict[str, EngineConfig]

    def __init__(
        self,
        *,
        api_server: DashboardServerPort | None = None,
        summary_updater: SummaryUpdateCallback | None = None,
        session_context: SessionContext,
        hooks: GameLifecycleHooks,
        db_service: DatabaseServicePort | None = None,
        engine_factory_service: EngineFactoryService,
        resource_poll_interval: float | None = None,
        resource_poll_max_interval: float | None = None,
        default_engine_handshake_timeout: float | None = None,
        engine_lifecycle: EngineLifecyclePolicy = "reuse",
    ) -> None:
        self.api_server: DashboardServerPort | None = api_server
        self._summary_updater: SummaryUpdateCallback | None = summary_updater
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()
        self.game_runner: GameRunner | None = None
        self.engine_pool: EnginePool | None = None
        self.instance_pool: InstancePool | None = session_context.instance_pool
        self.session_context: SessionContext = session_context
        self.db_service: DatabaseServicePort | None = db_service
        self._engine_factory_service = engine_factory_service
        self._hooks: GameLifecycleHooks = hooks
        self._remote_repo_ready: set[str] = set()
        self._remote_executors: dict[str, RemoteExecutor] = {}
        self._resource_poll_interval = resource_poll_interval
        self._resource_poll_max_interval = resource_poll_max_interval
        self._default_engine_handshake_timeout = default_engine_handshake_timeout
        self._engine_lifecycle = engine_lifecycle
        self.extra_options: JsonObject | None = None
        self.engine_configs = {}
        self._progress_hub = ProgressHub(
            api_server=self.api_server,
            preassign_worker=lambda numeric_game_id,
            num_workers,
            game_to_worker,
            worker_busy: _preassign_worker_service(
                numeric_game_id=numeric_game_id,
                num_workers=num_workers,
                game_to_worker=game_to_worker,
                worker_busy=worker_busy,
                logger=logger,
            ),
        )
        self._engine_option_snapshots: EngineOptionsSnapshots = {}
        self._engine_info_snapshots: EngineInfoSnapshots = {}

    async def run(self) -> None:  # pragma: no cover - to be implemented by subclasses
        raise NotImplementedError

    def _initialize_common_components(
        self,
        *,
        num_workers: int,
        engines: Sequence[EngineConfig],
        rules: RulesConfig,
        should_start_progress: bool = True,
    ) -> None:
        self.num_workers = num_workers
        self.progress_queue: asyncio.Queue[tuple[int, int, str | None]] = create_progress_queue()
        self.game_to_worker: dict[int, int] = {}
        self.worker_busy: set[int] = set()
        self.worker_snapshots: dict[int, WorkerSnapshotModel] = {}
        if self.api_server and should_start_progress:
            self.start_progress_consumer(
                num_workers=self.num_workers,
                progress_queue=self.progress_queue,
                game_to_worker=self.game_to_worker,
                worker_busy=self.worker_busy,
                worker_snapshots=self.worker_snapshots,
                on_summary_update=self._summary_updater,
            )
        self.game_runner = create_game_runner_from_rules(
            rules,
            list(engines),
            self.progress_queue,
        )
        self.game_runner.set_engine_options_callback(self._handle_engine_options)
        self.extra_options = compute_max_ply_extra_options(rules)

    def _handle_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, JsonValue],
        info: Mapping[str, str] | None,
    ) -> None:
        if not engine_name or not isinstance(options, Mapping):
            return
        try:
            serialized = coerce_json_object_serialized(options, field_name="engine_options")
        except (TypeError, ValueError):
            logger.debug("Failed to serialise USI options for %s", engine_name, exc_info=True)
            return
        self._engine_option_snapshots[engine_name] = serialized
        if info:
            self._engine_info_snapshots[engine_name] = {str(k): str(v) for k, v in info.items()}
        if self.api_server:
            try:
                self.api_server.update_engine_options(
                    engine_name,
                    serialized,
                    self._engine_info_snapshots.get(engine_name),
                )
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                logger.debug(
                    "Dashboard engine option update failed for %s: %s",
                    engine_name,
                    exc,
                    exc_info=True,
                )
        if self._summary_updater is not None:
            cb = self._summary_updater

            async def _trigger_summary() -> None:
                try:
                    await cb()
                except (RuntimeError, OSError, ValueError, TypeError) as exc:
                    logger.debug("Summary updater failed after engine option change: %s", exc, exc_info=True)

            try:
                asyncio.get_running_loop().create_task(_trigger_summary())
            except RuntimeError:
                logger.warning("No running event loop; executing summary updater synchronously")
                asyncio.run(_trigger_summary())

    def get_engine_option_snapshots(self) -> EngineOptionsSnapshots:
        return copy.deepcopy(self._engine_option_snapshots)

    def get_engine_info_snapshots(self) -> EngineInfoSnapshots:
        return {name: dict(info) for name, info in self._engine_info_snapshots.items()}

    def init_engine_pool_for_roles(self, num_workers: int, name_a: str, name_b: str) -> EnginePool:
        is_same_name = str(name_a) == str(name_b)
        cap = compute_pool_capacity(num_workers, is_same_name)
        return self.create_engine_pool(cap)

    def create_engine_pool(self, max_instances_per_engine: int) -> EnginePool:
        self.engine_pool = EnginePool(
            max_instances_per_engine=max_instances_per_engine,
            engine_factory_service=self._engine_factory_service,
            engine_configs=self.engine_configs,
            instance_pool=self.instance_pool,
            default_handshake_timeout=self._default_engine_handshake_timeout,
            lifecycle_policy=self._engine_lifecycle,
        )
        return self.engine_pool

    async def _notify_game_complete(self, event: GameCompletionEvent[_T]) -> None:
        await self._hooks.on_game_complete(event)
        if not await self._hooks.should_continue():
            self.request_stop()

    async def _emit_game_completion(
        self,
        *,
        game_id: str,
        game_info: rshogi.record.GameRecord,
        payload: _T,
        worker_idx: int | None,
    ) -> None:
        event = GameCompletionEvent(
            game_id=game_id,
            game_info=game_info,
            payload=payload,
            worker_idx=int(worker_idx) if worker_idx is not None else None,
            is_stop_requested=self._stop_event.is_set(),
        )
        await self._notify_game_complete(event)

    @dataclass
    class EngineGameSpec:
        pool_key: str
        config_path: Path
        extra_options: JsonObject | None
        instance_override: str | None = None
        role: Literal["black", "white"] = "black"

    @dataclass
    class GameExecutionSpec:
        black_item: BaseOrchestrator.EngineGameSpec
        white_item: BaseOrchestrator.EngineGameSpec
        initial_sfen: str
        game_id: str
        black_limits: TimeControlLimits | None
        white_limits: TimeControlLimits | None
        before_game_hook: BeforeGameHookPort | None = None
        game_round: int | None = None
        on_game_start: Callable[[], Awaitable[None]] | None = None

    async def shutdown(self) -> None:
        logger.debug("Shutting down orchestrator")
        gr = self.game_runner
        if gr is not None:
            gr.request_shutdown()
        await self._progress_hub.shutdown()

        wt = self._worker_tasks
        if wt:
            for task in list(wt):
                task.cancel()
            await asyncio.gather(*list(wt), return_exceptions=True)
            wt.clear()

        rt = self._running_tasks
        if rt:
            for task in list(rt):
                task.cancel()
            await asyncio.gather(*list(rt), return_exceptions=True)
            rt.clear()

        ep = self.engine_pool
        if ep is not None:
            await ep.shutdown_all()

    def request_stop(self) -> None:
        if not self._stop_event.is_set():
            self._stop_event.set()

    def start_progress_consumer(
        self,
        num_workers: int,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        game_to_worker: dict[int, int],
        worker_busy: set[int],
        worker_snapshots: dict[int, WorkerSnapshotModel],
        on_summary_update: SummaryUpdateCallback | None = None,
    ) -> None:
        _start_progress_consumer_service(
            self,
            num_workers=num_workers,
            progress_queue=progress_queue,
            game_to_worker=game_to_worker,
            worker_busy=worker_busy,
            worker_snapshots=worker_snapshots,
            on_summary_update=on_summary_update,
        )

    async def run_items_concurrently(
        self,
        items: list[_T],
        run_one: Callable[[_T], Awaitable[None]],
        concurrency_limit: int,
    ) -> None:
        await _run_items_concurrently_service(
            stop_event=self._stop_event,
            worker_tasks=self._worker_tasks,
            running_tasks=self._running_tasks,
            items=items,
            run_one=run_one,
            concurrency_limit=concurrency_limit,
        )


__all__ = [
    "BaseOrchestrator",
]
