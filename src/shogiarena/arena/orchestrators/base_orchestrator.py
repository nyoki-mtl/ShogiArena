"""Base orchestration support shared by tournament and SPSA runners."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.execution.engine_participant import EngineParticipant
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.instances.models import InstanceActiveGameSide
from shogiarena.arena.instances.pool import InstancePool, ResourceRequest
from shogiarena.arena.instances.slot_policy import estimate_required_slots
from shogiarena.arena.remote.executor import RemoteExecutor
from shogiarena.arena.services.persistence.records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
)
from shogiarena.arena.session import GameCompletionEvent, GameLifecycleHooks, SessionContext
from shogiarena.records import GameInfo

from . import base_orchestrator_utils
from .engine_pool import EnginePool
from .progress import ProgressHub, SummaryUpdateCallback
from .remote_exec import RemoteExecutionManager


@runtime_checkable
class DashboardServerProtocol(Protocol):
    """Protocol describing the dashboard API server interactions."""

    def set_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, Any], *, broadcast: bool = True) -> None: ...

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, Any]) -> None: ...

    def update_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, Any],
        info: Mapping[str, str] | None = None,
    ) -> None: ...


logger = logging.getLogger(__name__)


class BaseOrchestrator:
    """Base class offering convenience methods for orchestrators.

    This class intentionally keeps a very small API surface so that
    existing orchestrators (like TournamentOrchestrator) can remain as-is.
    """

    def __init__(
        self,
        *,
        api_server: DashboardServerProtocol | None = None,
        summary_updater: SummaryUpdateCallback | None = None,
        session_context: SessionContext,
        hooks: GameLifecycleHooks,
    ) -> None:
        # `api_server` is expected to provide `broadcast_worker_update` and
        # `set_worker_snapshot` methods compatible with the dashboard server.
        self.api_server: DashboardServerProtocol | None = api_server
        self._summary_updater: SummaryUpdateCallback | None = summary_updater
        # Common task groups and stop coordination
        self._worker_tasks: set[asyncio.Task[Any]] = set()
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._stop_event = asyncio.Event()
        # Optional attachments initialized by orchestrator subclasses
        self.game_runner: GameRunner | None = None
        self.engine_pool: EnginePool | None = None
        # Optional instance pool for remote/local instance selection
        self.instance_pool: InstancePool | None = session_context.instance_pool
        self.session_context: SessionContext = session_context
        services = session_context.services or {}
        self.db_service = services.get("db") if isinstance(services, Mapping) else None
        self._hooks: GameLifecycleHooks = hooks
        # Remote execution helpers shared across orchestrators
        self._remote_exec = RemoteExecutionManager()
        # Progress hub for SSE updates
        self._progress_hub = ProgressHub(
            api_server=self.api_server,
            preassign_worker=self.preassign_worker,
        )
        # Runtime engine snapshots propagated to dashboard/UI consumers
        self._engine_option_snapshots: dict[str, dict[str, Any]] = {}
        self._engine_info_snapshots: dict[str, dict[str, str]] = {}

    # --- Abstract entrypoint (implement in subclasses) -------------------
    async def run(self) -> object:  # pragma: no cover - to be implemented by subclasses
        raise NotImplementedError

    @staticmethod
    def normalize_options_list_to_dict(options: Any) -> dict[str, Any]:
        """Normalize a list-style options payload (e.g., from SPSA EngineSpec) into a dict.

        Supports [{"name": k, "value": v}, ...] and strings like
        "setoption name <k> value <v>".
        """
        out: dict[str, Any] = {}
        if not options:
            return out
        for o in options:
            if isinstance(o, dict) and "name" in o:
                out[str(o["name"])] = o.get("value", "")
            elif isinstance(o, str):
                s = o.strip()
                if s.startswith("setoption "):
                    parts = s.split()
                    i = parts.index("name")
                    k = parts[i + 1]
                    v = ""
                    if "value" in parts:
                        j = parts.index("value")
                        v = " ".join(parts[j + 1 :])
                    out[k] = v
        return out

    def get_remote_executor(self, remote_instance: Any) -> RemoteExecutor:
        """Return a cached RemoteExecutor for the provided instance."""
        return self._remote_exec.get_remote_executor(remote_instance)

    async def ensure_remote_repo(
        self,
        executor: RemoteExecutor,
        remote_instance: Any,
        remote_root: str | None = None,
    ) -> str:
        """Ensure the remote repository is cloned/updated once per instance and return its root."""
        return await self._remote_exec.ensure_remote_repo(executor, remote_instance, remote_root)

    async def resolve_remote_binaries(self, executor: RemoteExecutor, *config_paths: Path) -> list[str]:
        """Resolve local binaries and provision them to the remote host."""
        return await self._remote_exec.resolve_remote_binaries(executor, *config_paths)

    async def rewrite_remote_options(self, remote_instance: Any, *option_sets: dict[str, Any] | None) -> None:
        """Rewrite option dictionaries so that paths are valid for the remote environment."""
        await self._remote_exec.rewrite_remote_options(remote_instance, *option_sets)

    def build_remote_run_spec(
        self,
        *,
        game_id: str,
        start_sfen: str,
        black_name: str,
        white_name: str,
        black_engine_binary_path: str,
        white_engine_binary_path: str,
        black_options: dict[str, Any],
        white_options: dict[str, Any],
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        max_plies: int = 0,
    ) -> dict[str, Any]:
        """Build the JSON spec consumed by remote pair runners."""
        return self._remote_exec.build_remote_run_spec(
            game_id=game_id,
            start_sfen=start_sfen,
            black_name=black_name,
            white_name=white_name,
            black_engine_binary_path=black_engine_binary_path,
            white_engine_binary_path=white_engine_binary_path,
            black_options=black_options,
            white_options=white_options,
            black_limits=black_limits,
            white_limits=white_limits,
            max_plies=max_plies,
        )

    async def _prepare_remote_game_spec(
        self,
        *,
        remote_instance: Any,
        black_config_path: Path,
        white_config_path: Path,
        black_options: dict[str, Any] | None,
        white_options: dict[str, Any] | None,
        start_sfen: str,
        game_id: str,
        black_name: str,
        white_name: str,
        black_limits: TimeControlLimits,
        white_limits: TimeControlLimits,
        max_plies: int,
    ) -> tuple[RemoteExecutor, str, dict[str, Any]]:
        """Prepare executor, remote root, and run spec for remote pair execution."""
        return await self._remote_exec.prepare_remote_game_spec(
            remote_instance=remote_instance,
            black_config_path=black_config_path,
            white_config_path=white_config_path,
            black_options=black_options,
            white_options=white_options,
            start_sfen=start_sfen,
            game_id=game_id,
            black_name=black_name,
            white_name=white_name,
            black_limits=black_limits,
            white_limits=white_limits,
            max_plies=max_plies,
        )

    # --- Common initializer for orchestrators ----------------------------
    def _initialize_common_components(
        self,
        *,
        num_workers: int,
        engines: list[Any],
        rules: Any,
        start_progress: bool = True,
    ) -> None:
        """Initialize progress, worker state, GameRunner, and extra options.

        - Creates `progress_queue`, `game_to_worker`, `worker_busy`, `worker_snapshots`.
        - Starts progress consumer if `api_server` is available and `start_progress` is True.
        - Creates `game_runner` from rules and engines.
        - Computes `extra_options` for max-move synchronization.
        """
        self.num_workers = num_workers
        self.progress_queue: asyncio.Queue[tuple[int, int, str | None]] = (
            base_orchestrator_utils.create_progress_queue()
        )
        self.game_to_worker: dict[int, int] = {}
        self.worker_busy: set[int] = set()
        self.worker_snapshots: dict[int, dict[str, Any]] = {}
        if self.api_server and start_progress:
            self.start_progress_consumer(
                num_workers=self.num_workers,
                progress_queue=self.progress_queue,
                game_to_worker=self.game_to_worker,
                worker_busy=self.worker_busy,
                worker_snapshots=self.worker_snapshots,
                on_summary_update=self._summary_updater,
            )
        self.game_runner = base_orchestrator_utils.create_game_runner_from_rules(
            rules,
            engines,
            self.progress_queue,
        )
        if hasattr(self.game_runner, "set_engine_options_callback"):
            self.game_runner.set_engine_options_callback(self._handle_engine_options)
        self.extra_options = base_orchestrator_utils.compute_max_ply_extra_options(rules)

    # --- Runtime engine snapshots ---------------------------------------
    def _handle_engine_options(
        self,
        engine_name: str,
        options: Mapping[str, Any],
        info: Mapping[str, str] | None,
    ) -> None:
        """Persist latest USI option snapshot for dashboard/API consumers."""

        if not engine_name or not isinstance(options, Mapping):
            return
        try:
            serialized = json.loads(json.dumps(options, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.debug("Failed to serialise USI options for %s", engine_name, exc_info=True)
            return
        self._engine_option_snapshots[engine_name] = serialized
        if info:
            self._engine_info_snapshots[engine_name] = {str(k): str(v) for k, v in info.items()}
        if self.api_server and hasattr(self.api_server, "update_engine_options"):
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
                # no running loop (e.g. shutdown); ignore
                pass

    def get_engine_option_snapshots(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of captured USI option snapshots."""

        return copy.deepcopy(self._engine_option_snapshots)

    def get_engine_info_snapshots(self) -> dict[str, dict[str, str]]:
        """Return a shallow copy of engine info captured during handshake."""

        return {name: dict(info) for name, info in self._engine_info_snapshots.items()}

    def init_engine_pool_for_roles(self, num_workers: int, name_a: str, name_b: str) -> EnginePool:
        """Initialize EnginePool with capacity tuned for role-paired engines.

        When both roles use the same engine name, capacity is doubled to avoid deadlocks.
        """
        same_name = str(name_a) == str(name_b)
        cap = base_orchestrator_utils.compute_pool_capacity(num_workers, same_name)
        return self.create_engine_pool(cap)

    def create_engine_pool(self, max_instances_per_engine: int) -> EnginePool:
        """Factory to create and attach an EnginePool with given capacity."""
        engine_configs = getattr(self, "engine_configs", None)
        self.engine_pool = EnginePool(
            max_instances_per_engine=max_instances_per_engine,
            engine_configs=engine_configs,
            instance_pool=self.instance_pool,
        )
        return self.engine_pool

    async def _notify_game_complete(self, event: GameCompletionEvent) -> None:
        """Forward a completion event to runner-provided lifecycle hooks."""

        await self._hooks.on_game_complete(event)
        if not await self._hooks.should_continue():
            self.request_stop()

    # --- Engine game item -------------------------------------------------
    @dataclass
    class EngineGameSpec:
        pool_key: str
        config_path: Path
        extra_options: dict[str, Any] | None
        instance_override: str | None = None
        role: Literal["black", "white"] = "black"

    # --- Single game execution (shared) ----------------------------------
    async def _execute_game(
        self,
        *,
        black_item: BaseOrchestrator.EngineGameSpec,
        white_item: BaseOrchestrator.EngineGameSpec,
        initial_sfen: str,
        game_id: str,
        black_limits: TimeControlLimits | None,
        white_limits: TimeControlLimits | None,
        before_game_hook: (
            Callable[[dict[str, AsyncUsiEngine]], Awaitable[dict[AsyncUsiEngine, str] | None]] | None
        ) = None,
        game_round: int | None = None,
        on_game_start: Callable[[], Awaitable[None]] | None = None,
    ) -> GameInfo:
        """Acquire engines, optionally apply pre-run hook, run a single game, and release engines.

        Args:
            black_item: (pool_key, engine_config_path, extra_options) for black side
            white_item: (pool_key, engine_config_path, extra_options) for white side
            initial_sfen: Starting SFEN
            game_id: Game identifier
            black_limits: TimeControl limits for black (may be None)
            white_limits: TimeControl limits for white (may be None)
            before_game_hook: Optional async function that receives a mapping
                {pool_key -> engine_instance} and may return a mapping
                {engine_instance -> temporary_name} to be applied during the game

        Returns:
            GameInfo object from GameRunner
        """
        ep = self.engine_pool
        assert ep is not None, "EnginePool not initialized"
        gr = self.game_runner
        assert gr is not None, "GameRunner not initialized"

        # Track instance slot usage for visibility. We do not block yet.
        # Count how many engine processes per instance this game will use.
        resource_requirements: dict[str, ResourceRequest] = {}
        slots_reserved = False
        instance_role_map: dict[str, list[InstanceActiveGameSide]] = {}
        pool = self.instance_pool
        engine_configs = getattr(self, "engine_configs", None)
        black_engine_spec = None
        white_engine_spec = None
        if pool is not None:
            resource_requirements = self._collect_instance_usage(pool, black_item, white_item)
            if resource_requirements:
                await self._await_instance_resources(pool, resource_requirements, game_id)
                slots_reserved = True
            if engine_configs is None:
                raise AttributeError("engine_configs not initialized on orchestrator")

            black_key = black_item.pool_key.split("#", 1)[0]
            white_key = white_item.pool_key.split("#", 1)[0]
            if black_key not in engine_configs or white_key not in engine_configs:
                raise KeyError("Engine config missing while recording active games")

            black_engine_spec = engine_configs[black_key]
            white_engine_spec = engine_configs[white_key]

            black_instance_id = (
                getattr(black_item, "instance_override", None)
                or getattr(black_engine_spec, "instance_id", None)
                or "local"
            )
            white_instance_id = (
                getattr(white_item, "instance_override", None)
                or getattr(white_engine_spec, "instance_id", None)
                or "local"
            )

            # Ensure instances exist (local fallback when necessary)
            if pool.get_instance(black_instance_id) is None:
                if black_instance_id == "local":
                    pool.ensure_local_instance()
                else:
                    raise KeyError(f"instance '{black_instance_id}' is not registered in the instance pool")
            if pool.get_instance(white_instance_id) is None:
                if white_instance_id == "local":
                    pool.ensure_local_instance()
                else:
                    raise KeyError(f"instance '{white_instance_id}' is not registered in the instance pool")

            instance_role_map.setdefault(
                black_instance_id,
                [],
            ).append(
                InstanceActiveGameSide(
                    role="black", engine_name=str(black_engine_spec.name), pool_key=black_item.pool_key
                )
            )
            instance_role_map.setdefault(
                white_instance_id,
                [],
            ).append(
                InstanceActiveGameSide(
                    role="white", engine_name=str(white_engine_spec.name), pool_key=white_item.pool_key
                )
            )

            black_tc_spec = black_limits.to_spec_str() if black_limits is not None else None
            white_tc_spec = white_limits.to_spec_str() if white_limits is not None else None

            for inst_id, roles in instance_role_map.items():
                for role in roles:
                    pool.record_active_game(
                        inst_id,
                        game_id=game_id,
                        black_engine=str(black_engine_spec.name)
                        if black_engine_spec is not None
                        else black_item.pool_key,
                        white_engine=str(white_engine_spec.name)
                        if white_engine_spec is not None
                        else white_item.pool_key,
                        initial_sfen=initial_sfen,
                        role=role,
                        round_index=game_round,
                        time_control_black=black_tc_spec,
                        time_control_white=white_tc_spec,
                    )

        if hasattr(gr, "set_latency_callback"):
            gr.set_latency_callback(None)

        # Convert specs to tuples for EnginePool API
        b_tuple = (
            black_item.pool_key,
            black_item.config_path,
            black_item.extra_options,
            getattr(black_item, "instance_override", None),
        )
        w_tuple = (
            white_item.pool_key,
            white_item.config_path,
            white_item.extra_options,
            getattr(white_item, "instance_override", None),
        )

        black_engine: AsyncUsiEngine | None = None
        white_engine: AsyncUsiEngine | None = None

        try:
            black_engine, white_engine = await ep.acquire_pair_sorted(b_tuple, w_tuple)

            # Build map for hooks to identify engines by their pool keys
            engines_by_key: dict[str, AsyncUsiEngine] = {
                black_item.pool_key: black_engine,
                white_item.pool_key: white_engine,
            }

            # Optional temporary name overrides from hook
            temp_names: dict[AsyncUsiEngine, str] | None = None
            if before_game_hook is not None:
                # Let hook errors propagate; do not swallow unexpected failures
                maybe_names = await before_game_hook(engines_by_key)
                if isinstance(maybe_names, dict) and maybe_names:
                    temp_names = maybe_names

            def pick_display_name(spec: Any | None, pool_key: str, engine: AsyncUsiEngine) -> str:
                spec_name = getattr(spec, "name", None)
                if isinstance(spec_name, str) and spec_name:
                    return spec_name
                if isinstance(pool_key, str) and "#" in pool_key:
                    return pool_key.split("#", 1)[0]
                return engine.name

            black_override = temp_names.get(black_engine) if temp_names else None
            if not black_override:
                black_override = pick_display_name(black_engine_spec, black_item.pool_key, black_engine)

            white_override = temp_names.get(white_engine) if temp_names else None
            if not white_override:
                white_override = pick_display_name(white_engine_spec, white_item.pool_key, white_engine)

            black_participant = EngineParticipant(
                black_engine,
                name_override=black_override,
                role="black",
            )
            white_participant = EngineParticipant(
                white_engine,
                name_override=white_override,
                role="white",
            )

            started_at = datetime.now(timezone.utc)

            if on_game_start is not None:
                await on_game_start()
            game_info = await gr.run_game(
                black_participant,
                white_participant,
                initial_sfen,
                game_id,
                black_time_control_limits=black_limits,
                white_time_control_limits=white_limits,
            )
            completed_at = datetime.now(timezone.utc)
            participation_records = self._collect_participation_records_local(
                black_engine=black_engine,
                white_engine=white_engine,
                black_participant=black_participant,
                white_participant=white_participant,
                black_spec=black_engine_spec,
                white_spec=white_engine_spec,
                black_pool_key=black_item.pool_key,
                white_pool_key=white_item.pool_key,
                started_at=started_at,
                completed_at=completed_at,
            )
            if participation_records:
                game_info._arena_participation = participation_records
            return game_info
        finally:
            try:
                gr.set_latency_callback(None)
            except (RuntimeError, TypeError) as exc:
                logger.debug("Failed to reset latency callback: %s", exc, exc_info=True)
            if black_engine is not None and white_engine is not None:
                try:
                    await ep.release(white_item.pool_key, white_engine, white_item.instance_override)
                finally:
                    await ep.release(black_item.pool_key, black_engine, black_item.instance_override)
            # Release instance usage marks
            pool = self.instance_pool
            if pool is not None:
                if slots_reserved and resource_requirements:
                    pool.release_resources(resource_requirements)
                if instance_role_map:
                    for inst_id in instance_role_map:
                        pool.clear_active_game(inst_id, game_id)

    def _collect_participation_records_local(
        self,
        *,
        black_engine: AsyncUsiEngine,
        white_engine: AsyncUsiEngine,
        black_participant: EngineParticipant,
        white_participant: EngineParticipant,
        black_spec: Any | None,
        white_spec: Any | None,
        black_pool_key: str,
        white_pool_key: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> list[GameParticipationRecord]:
        records: list[GameParticipationRecord] = []
        black_config = getattr(black_engine, "config", None)
        white_config = getattr(white_engine, "config", None)
        records.append(
            self._construct_participation_record(
                role="black",
                engine_name=str(getattr(black_spec, "name", black_engine.name)),
                display_name=black_participant.name,
                spec=black_spec,
                engine_config=black_config,
                binary_path=getattr(black_config, "engine_path", None),
                instance_id=(getattr(black_spec, "instance_id", None) or "local"),
                started_at=started_at,
                completed_at=completed_at,
                pool_key=black_pool_key,
                engine_options=getattr(black_config, "options", None),
                go_options=getattr(black_config, "go_options", None),
                environment=getattr(black_config, "environment", None),
                engine_info=dict(getattr(black_engine, "engine_info", {})),
            )
        )
        records.append(
            self._construct_participation_record(
                role="white",
                engine_name=str(getattr(white_spec, "name", white_engine.name)),
                display_name=white_participant.name,
                spec=white_spec,
                engine_config=white_config,
                binary_path=getattr(white_config, "engine_path", None),
                instance_id=(getattr(white_spec, "instance_id", None) or "local"),
                started_at=started_at,
                completed_at=completed_at,
                pool_key=white_pool_key,
                engine_options=getattr(white_config, "options", None),
                go_options=getattr(white_config, "go_options", None),
                environment=getattr(white_config, "environment", None),
                engine_info=dict(getattr(white_engine, "engine_info", {})),
            )
        )
        return records

    def _engine_artifact_snapshot(
        self,
        *,
        engine_name: str,
        spec: Any | None,
        engine_config: Any | None,
        binary_path: str | None,
    ) -> EngineArtifactSnapshot:
        artifact: str | None = None
        build_flags: dict[str, Any] = {}
        metadata: dict[str, Any] = {}

        if spec is not None:
            artifact = getattr(spec, "artifact", None) or artifact
            build_options = getattr(spec, "build_options", None)
            if isinstance(build_options, Mapping):
                build_flags.update(build_options)
            cfg_path = getattr(spec, "engine_config", None)
            if cfg_path is not None:
                metadata["engine_config_path"] = str(cfg_path)
            overlays = getattr(spec, "options_overlays", None)
            if overlays:
                metadata["options_overlays"] = [str(p) for p in overlays]
            inst_id = getattr(spec, "instance_id", None)
            if inst_id:
                metadata["requested_instance_id"] = str(inst_id)

        if engine_config is not None:
            cfg_artifact = getattr(engine_config, "artifact", None)
            if cfg_artifact:
                artifact = cfg_artifact
            cfg_build = getattr(engine_config, "build_options", None)
            if isinstance(cfg_build, Mapping):
                build_flags.update(cfg_build)
            exec_path = getattr(engine_config, "engine_path", None)
            if exec_path and not binary_path:
                binary_path = exec_path
            working_dir = getattr(engine_config, "working_directory", None)
            if working_dir:
                metadata["working_directory"] = str(working_dir)

        metadata = {k: v for k, v in metadata.items() if v is not None}
        build_flags_payload: dict[str, Any] | None = build_flags or None
        return EngineArtifactSnapshot(
            logical_name=engine_name,
            artifact=artifact,
            binary_path=binary_path,
            build_flags=build_flags_payload,
            metadata=metadata or None,
        )

    def _instance_snapshot(self, instance_id: str | None) -> InstanceSnapshot | None:
        if not instance_id:
            return None
        pool = self.instance_pool
        if pool is None:
            return InstanceSnapshot(instance_id=instance_id)
        inst = pool.get_instance(instance_id)
        if inst is None:
            if instance_id == "local":
                inst = pool.ensure_local_instance()
            else:
                return InstanceSnapshot(instance_id=instance_id)
        metrics = inst.metrics
        tags = tuple(inst.config.tags or [])
        extra: dict[str, Any] = {
            "slots": inst.config.slots,
            "reachable": metrics.reachable,
        }
        if inst.source_path is not None:
            extra["config_path"] = str(inst.source_path)
        if inst.drain:
            extra["draining"] = True
        return InstanceSnapshot(
            instance_id=inst.name,
            display_name=inst.name,
            host_label=inst.config.host,
            cpu_model=metrics.cpu_model,
            cpu_arch=None,
            cpu_cores=metrics.cpu_count,
            cpu_threads=metrics.cpu_count,
            memory_total_mb=metrics.mem_total_mb,
            os_info=None,
            gpu_model=None,
            gpu_vendor=None,
            gpu_vram_mb=None,
            gpu_count=None,
            instance_type=inst.type.value,
            tags=tags,
            extra=extra,
        )

    def _construct_participation_record(
        self,
        *,
        role: Literal["black", "white"],
        engine_name: str,
        display_name: str | None,
        spec: Any | None,
        engine_config: Any | None,
        binary_path: str | None,
        instance_id: str | None,
        started_at: datetime,
        completed_at: datetime,
        pool_key: str | None,
        engine_options: Mapping[str, Any] | None,
        go_options: Mapping[str, Any] | None,
        environment: Mapping[str, Any] | None,
        engine_info: Mapping[str, Any] | None,
    ) -> GameParticipationRecord:
        artifact_snapshot = self._engine_artifact_snapshot(
            engine_name=engine_name,
            spec=spec,
            engine_config=engine_config,
            binary_path=binary_path,
        )
        instance_snapshot = self._instance_snapshot(instance_id)

        build_flags: dict[str, Any] = {}
        if spec is not None:
            spec_build = getattr(spec, "build_options", None)
            if isinstance(spec_build, Mapping):
                build_flags.update(spec_build)
        if engine_config is not None:
            cfg_build = getattr(engine_config, "build_options", None)
            if isinstance(cfg_build, Mapping):
                build_flags.update(cfg_build)

        extras: dict[str, Any] = {}
        if engine_options:
            extras["engine_options"] = dict(engine_options)
        if go_options:
            extras["go_options"] = dict(go_options)
        if environment:
            extras["environment"] = dict(environment)
        if engine_info:
            extras["engine_info"] = dict(engine_info)
        if pool_key:
            extras["pool_key"] = pool_key
        extras = {k: v for k, v in extras.items() if v}

        return GameParticipationRecord(
            role=role,
            engine_name=engine_name,
            engine_display_name=display_name,
            engine_artifact=artifact_snapshot,
            instance=instance_snapshot,
            binary_path=binary_path,
            build_flags=build_flags or None,
            started_at=started_at,
            completed_at=completed_at,
            run_id=getattr(self.session_context, "run_id", None),
            extra=extras or None,
        )

    # --- Worker assignment helpers ----------------------------------------
    def _collect_instance_usage(
        self, pool: InstancePool, *items: BaseOrchestrator.EngineGameSpec
    ) -> dict[str, ResourceRequest]:
        if not hasattr(self, "engine_configs"):
            raise AttributeError("engine_configs not initialized on orchestrator")
        engine_configs = self.engine_configs
        if not isinstance(engine_configs, Mapping):
            raise TypeError("engine_configs must be a mapping of engine name to config spec")
        typed_configs = cast(Mapping[str, object], engine_configs)

        usage: dict[str, ResourceRequest] = {}
        for item in items:
            pool_key = item.pool_key
            engine_name = pool_key.split("#", 1)[0]
            if engine_name not in typed_configs:
                raise KeyError(f"engine '{engine_name}' is missing in engine_configs")
            spec = typed_configs[engine_name]
            override = getattr(item, "instance_override", None)
            instance_id = override or getattr(spec, "instance_id", None) or "local"
            instance = pool.get_instance(instance_id)
            if instance is None:
                if instance_id == "local":
                    instance = pool.ensure_local_instance()
                else:
                    raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")
            slots_required = estimate_required_slots(spec, item.extra_options)
            current = usage.get(instance_id)
            if current is None:
                usage[instance_id] = ResourceRequest(slots=slots_required, engines=1)
            else:
                usage[instance_id] = ResourceRequest(
                    slots=current.slots + slots_required,
                    engines=current.engines + 1,
                )
        return usage

    async def _await_instance_resources(
        self,
        pool: InstancePool,
        requirements: Mapping[str, ResourceRequest],
        game_id: str,
        *,
        poll_interval: float = 0.1,
        max_interval: float = 1.0,
    ) -> None:
        """Wait until all required resources are available, reserving them atomically."""

        # Validate hard capacity upfront to avoid waiting forever
        for instance_id, req in requirements.items():
            if req.slots <= 0 and req.engines <= 0:
                continue
            instance = pool.get_instance(instance_id)
            if instance is None:
                if instance_id == "local":
                    instance = pool.ensure_local_instance()
                else:
                    raise KeyError(f"instance '{instance_id}' is not registered in the instance pool")
            slot_capacity = instance.effective_slots
            if slot_capacity is not None and req.slots and slot_capacity < req.slots:
                raise RuntimeError(
                    f"Game {game_id} requires {req.slots} slot(s) on instance '{instance_id}' "
                    f"but the instance exposes only {slot_capacity} slot(s). "
                    "Adjust either engine Threads or instance slots."
                )
            max_engines = instance.max_engine_capacity
            if instance.engine_capacity_known and req.engines and max_engines < req.engines:
                raise RuntimeError(
                    f"Game {game_id} requires {req.engines} engine(s) on instance '{instance_id}' "
                    f"but the instance allows only {max_engines} concurrent engine(s). "
                    "Adjust the instance's max_engines or redistribute engines."
                )

        delay = poll_interval
        while True:
            acquired = pool.try_acquire_resources(requirements)
            if acquired:
                return

            if self._stop_event.is_set():
                raise RuntimeError(f"Stop requested while waiting for instance resources (game {game_id})")

            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_interval)

    # --- Worker assignment helpers ----------------------------------------
    def preassign_worker(
        self,
        numeric_game_id: int,
        num_workers: int,
        game_to_worker: dict[int, int],
        worker_busy: set[int],
    ) -> int | None:
        """Assign a stable worker index for a game if not already assigned.

        Returns the assigned worker index, or None if all workers are busy.

        Note: We avoid round-robin fallback to prevent mapping multiple
        concurrent games onto the same worker card, which caused the
        dashboard to alternate between different games on one card.
        """
        if numeric_game_id in game_to_worker:
            return game_to_worker[numeric_game_id]
        for idx in range(num_workers):
            if idx not in worker_busy:
                game_to_worker[numeric_game_id] = idx
                worker_busy.add(idx)
                logger.debug("Pre-assigned worker %s to game %s", idx, numeric_game_id)
                return idx
        # All workers busy: defer assignment until a worker frees
        logger.debug("All workers busy; deferring assignment for game %s", numeric_game_id)
        return None

    # --- Callback helpers --------------------------------------------------
    @staticmethod
    async def notify_callback(cb: Callable[..., Any] | None, *args: Any, **kwargs: Any) -> None:
        """Invoke a callback and surface failures with context."""
        if not cb:
            return
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb(*args, **kwargs)
            else:
                cb(*args, **kwargs)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            name = getattr(cb, "__name__", repr(cb))
            logger.exception("callback %s failed: %s", name, exc)
            raise

    # --- Common shutdown ---------------------------------------------------
    async def shutdown(self) -> None:
        """Common shutdown routine for orchestrators.

        - Hints GameRunner to stop extra USI chatter
        - Cancels progress consumer task if present
        - Cancels worker/running task sets if present
        - Shuts down EnginePool
        """
        logger.debug("Shutting down orchestrator")

        # Hint game runner to avoid sending further USI commands
        gr = self.game_runner
        if gr is not None:
            gr.request_shutdown()

        await self._progress_hub.shutdown()

        # Cancel worker loops and any running game tasks
        wt = self._worker_tasks
        if wt:
            for t in list(wt):
                t.cancel()
            await asyncio.gather(*list(wt), return_exceptions=True)
            wt.clear()

        rt = self._running_tasks
        if rt:
            for t in list(rt):
                t.cancel()
            await asyncio.gather(*list(rt), return_exceptions=True)
            rt.clear()

        # Shutdown engine pool
        ep = self.engine_pool
        if ep is not None:
            await ep.shutdown_all()

    # --- Stop coordination -------------------------------------------------
    def request_stop(self) -> None:
        """Signal orchestrator to stop scheduling new items; in-flight continue."""
        if not self._stop_event.is_set():
            self._stop_event.set()

    # --- Progress consumer task management --------------------------------
    def start_progress_consumer(
        self,
        num_workers: int,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        game_to_worker: dict[int, int],
        worker_busy: set[int],
        worker_snapshots: dict[int, dict[str, Any]],
        on_summary_update: SummaryUpdateCallback | None = None,
    ) -> None:
        """Start shared progress consumer via ProgressHub."""
        self._progress_hub.update_api_server(self.api_server)
        self._progress_hub.start(
            num_workers=num_workers,
            progress_queue=progress_queue,
            game_to_worker=game_to_worker,
            worker_busy=worker_busy,
            worker_snapshots=worker_snapshots,
            on_summary_update=on_summary_update,
        )

    # --- Generic worker scheduler -----------------------------------------
    async def run_items_concurrently(
        self,
        items: list[Any],
        run_one: Callable[[Any], Awaitable[None]],
        concurrency_limit: int,
    ) -> None:
        """Run items concurrently with a semaphore and stop support."""
        if not items:
            return
        semaphore = asyncio.Semaphore(concurrency_limit)
        lock = asyncio.Lock()
        idx = {"i": 0}

        async def next_item() -> Any | None:
            async with lock:
                if self._stop_event.is_set():
                    return None
                if idx["i"] >= len(items):
                    return None
                it = items[idx["i"]]
                idx["i"] += 1
                return it

        async def worker() -> None:
            while not self._stop_event.is_set():
                it = await next_item()
                if it is None:
                    break
                async with semaphore:

                    async def _invoke(x: Any) -> None:
                        await run_one(x)

                    t: asyncio.Task[Any] = asyncio.create_task(_invoke(it))
                    self._running_tasks.add(t)
                    await t
                    self._running_tasks.discard(t)

        workers = [asyncio.create_task(worker()) for _ in range(concurrency_limit)]
        self._worker_tasks.update(workers)
        await asyncio.gather(*workers)
        for w in workers:
            self._worker_tasks.discard(w)
