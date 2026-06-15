"""Engine pool for managing reusable AsyncUsiEngine instances with bounded parallelism."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import EngineLifecyclePolicy
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class _EngineSpec(Protocol):
    instance_id: str | None
    cpu_affinity: tuple[int, ...] | None
    handshake_timeout: float | None


class EnginePool:
    """Manage reusable ``AsyncUsiEngine`` instances with bounded parallelism."""

    def __init__(
        self,
        max_instances_per_engine: int = 2,
        *,
        engine_factory_service: EngineFactoryService,
        engine_configs: Mapping[str, _EngineSpec] | None = None,
        instance_pool: InstancePool | None = None,
        default_handshake_timeout: float | None = None,
        lifecycle_policy: EngineLifecyclePolicy = "reuse",
    ) -> None:
        if lifecycle_policy not in ("reuse", "per_game"):
            raise ValueError("lifecycle_policy must be 'reuse' or 'per_game'")
        self.max_instances = max_instances_per_engine
        self.lifecycle_policy = lifecycle_policy
        self._engine_factory_service = engine_factory_service
        self.pools: dict[str, list[AsyncUsiEngine]] = {}
        self.in_use: dict[str, set[AsyncUsiEngine]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiters: set[asyncio.Event] = set()
        self._engine_configs: Mapping[str, _EngineSpec] = engine_configs or {}
        self._instance_pool = instance_pool
        self._engine_instance_ids: dict[AsyncUsiEngine, str] = {}
        self._default_handshake_timeout = default_handshake_timeout

    def _resolve_handshake_timeout(self, spec: _EngineSpec | None) -> float:
        candidates = []
        if spec is not None:
            candidates.append(spec.handshake_timeout)
        candidates.append(self._default_handshake_timeout)
        for value in candidates:
            if value is None:
                continue
            try:
                resolved = float(value)
            except (TypeError, ValueError) as exc:
                raise TypeError(f"handshake_timeout must be a number; got {value!r}") from exc
            if resolved <= 0:
                raise ValueError("handshake_timeout must be positive")
            return resolved
        return AsyncUsiEngine.DEFAULT_HANDSHAKE_TIMEOUT

    def _resolve_instance_id(self, engine_name: str, instance_override: str | None) -> str | None:
        if instance_override is not None:
            return instance_override
        base_name = engine_name.split("#", 1)[0]
        spec = self._engine_configs.get(base_name)
        return spec.instance_id if spec is not None else None

    def _resolve_instance_by_id(self, instance_id: str) -> Instance | None:
        if self._instance_pool is None:
            return None
        instance = self._instance_pool.get_instance(instance_id)
        if instance is None and instance_id == "local":
            instance = self._instance_pool.ensure_local_instance()
        return instance

    def _resolve_instance(self, engine_name: str, instance_override: str | None) -> Instance | None:
        if self._instance_pool is None:
            return None
        instance_id = self._resolve_instance_id(engine_name, instance_override)
        if instance_id:
            return self._resolve_instance_by_id(instance_id)
        return self._instance_pool.ensure_local_instance()

    def _detach_engine_instance(self, engine: AsyncUsiEngine) -> None:
        instance_id = self._engine_instance_ids.pop(engine, None)
        if not instance_id:
            return
        instance = self._resolve_instance_by_id(instance_id)
        if instance is not None:
            instance.remove_engine_processes(1)

    def _notify_waiters(self) -> None:
        for wait_event in tuple(self._waiters):
            wait_event.set()

    def _log_pool_state(self, slot_key: str, action: str) -> None:
        logger.debug(
            "%s for %s (active=%d idle=%d lifecycle=%s)",
            action,
            slot_key,
            len(self.in_use.get(slot_key, set())),
            len(self.pools.get(slot_key, [])),
            self.lifecycle_policy,
        )

    async def _evict_idle_engine_for_instance(
        self,
        instance_id: str,
        *,
        exclude_slot_key: str | None = None,
    ) -> bool:
        for candidate_slot_key in tuple(self.pools.keys()):
            if exclude_slot_key is not None and candidate_slot_key == exclude_slot_key:
                continue
            lock = self._locks.get(candidate_slot_key)
            if lock is None:
                continue

            engine_to_close: AsyncUsiEngine | None = None
            async with lock:
                pool = self.pools.get(candidate_slot_key)
                if not pool:
                    continue
                for index in range(len(pool) - 1, -1, -1):
                    candidate = pool[index]
                    if self._engine_instance_ids.get(candidate) != instance_id:
                        continue
                    engine_to_close = pool.pop(index)
                    break

            if engine_to_close is None:
                continue

            try:
                await engine_to_close.close()
            except (TimeoutError, OSError, RuntimeError) as exc:
                logger.debug(
                    "Error closing evicted engine from %s: %s",
                    candidate_slot_key,
                    exc,
                    exc_info=True,
                )
            self._detach_engine_instance(engine_to_close)
            logger.debug(
                "Evicted idle engine from %s on instance %s to free capacity",
                candidate_slot_key,
                instance_id,
            )
            return True

        return False

    @staticmethod
    def slot_key(engine_key: str, instance_override: str | None) -> str:
        override = (instance_override or "auto").strip()
        return f"{engine_key}@{override}" if override else f"{engine_key}@auto"

    @staticmethod
    def _slot_key(engine_key: str, instance_override: str | None) -> str:
        return EnginePool.slot_key(engine_key, instance_override)

    async def acquire(
        self,
        engine_name: str,
        config_path: Path,
        extra_options: JsonObject | None = None,
        instance_override: str | None = None,
    ) -> AsyncUsiEngine:
        slot_key = self._slot_key(engine_name, instance_override)
        if slot_key not in self._locks:
            self._locks[slot_key] = asyncio.Lock()

        while True:
            capacity_blocked_instance: str | None = None
            async with self._locks[slot_key]:
                pool = self.pools.setdefault(slot_key, [])
                in_use = self.in_use.setdefault(slot_key, set())

                while pool:
                    engine = pool.pop()
                    if engine.is_running:
                        in_use.add(engine)
                        self._log_pool_state(slot_key, "Reused engine from pool")
                        return engine
                    try:
                        await engine.close()
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        logger.debug(
                            "Discarded closed engine %s during acquire: %s",
                            slot_key,
                            exc,
                            exc_info=True,
                        )
                    self._detach_engine_instance(engine)

                total_instances = len(pool) + len(in_use)
                if total_instances < self.max_instances:
                    logger.debug("Creating new engine instance for %s", slot_key)
                    base_name = engine_name.split("#", 1)[0]
                    spec = self._engine_configs.get(base_name)
                    instance_id = spec.instance_id if spec is not None else None
                    affinity = spec.cpu_affinity if spec is not None else None
                    if instance_override is not None:
                        instance_id = instance_override
                    instance = self._resolve_instance(engine_name, instance_override)
                    is_capacity_blocked = False
                    if instance is not None:
                        capacity = instance.max_engine_capacity
                        if instance.metrics.engine_processes >= capacity:
                            logger.debug(
                                "Instance %s at engine capacity (%s); waiting...",
                                instance.name,
                                capacity,
                            )
                            is_capacity_blocked = True
                            capacity_blocked_instance = instance.name

                    if not is_capacity_blocked:
                        reserved_instance = instance
                        if reserved_instance is not None:
                            reserved_instance.add_engine_processes(1)
                        handshake_timeout = self._resolve_handshake_timeout(spec)
                        try:
                            engine = await self._engine_factory_service.create_engine(
                                config_path,
                                timeout=handshake_timeout,
                                extra_options=extra_options,
                                engine_name=engine_name,
                                instance_id=instance_id,
                                instance_pool=self._instance_pool,
                                cpu_affinity=affinity,
                            )
                        except BaseException:
                            if reserved_instance is not None:
                                reserved_instance.remove_engine_processes(1)
                                self._notify_waiters()
                            raise
                        if reserved_instance is not None:
                            self._engine_instance_ids[engine] = reserved_instance.name
                        in_use.add(engine)
                        self._log_pool_state(slot_key, "Created engine instance")
                        return engine

            if capacity_blocked_instance is not None:
                reclaimed = await self._evict_idle_engine_for_instance(
                    capacity_blocked_instance,
                    exclude_slot_key=slot_key,
                )
                if reclaimed:
                    self._notify_waiters()
                    continue

            logger.debug("Pool for %s at capacity (%s), waiting...", slot_key, self.max_instances)
            wait_event = asyncio.Event()
            self._waiters.add(wait_event)
            try:
                try:
                    await asyncio.wait_for(wait_event.wait(), timeout=0.5)
                except TimeoutError:
                    logger.debug("Timed out waiting for pool signal for %s; retrying", slot_key)
            finally:
                self._waiters.discard(wait_event)

    async def release(self, engine_name: str, engine: AsyncUsiEngine, instance_override: str | None = None) -> None:
        slot_key = self._slot_key(engine_name, instance_override)
        if slot_key not in self._locks:
            return

        async with self._locks[slot_key]:
            in_use = self.in_use.get(slot_key)
            if not in_use or engine not in in_use:
                return

            pool = self.pools.setdefault(slot_key, [])
            in_use.remove(engine)

            if self.lifecycle_policy == "reuse" and len(pool) < self.max_instances and engine.is_running:
                pool.append(engine)
                self._log_pool_state(slot_key, "Returned engine to pool")
            else:
                try:
                    await engine.close()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    logger.debug("Error closing engine %s during release: %s", slot_key, exc, exc_info=True)
                if self.lifecycle_policy == "per_game":
                    self._log_pool_state(slot_key, "Closed engine after game")
                else:
                    self._log_pool_state(slot_key, "Terminated excess engine instance")
                self._detach_engine_instance(engine)

            self._notify_waiters()

    async def shutdown_all(self) -> None:
        logger.debug("Shutting down all engines")
        for engine_name, pool in list(self.pools.items()):
            logger.debug("Shutting down pool for %s", engine_name)
            for engine in list(pool):
                try:
                    await engine.close()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    logger.debug("Error closing pooled engine %s: %s", engine_name, exc, exc_info=True)
                self._detach_engine_instance(engine)
            for engine in list(self.in_use.get(engine_name, set())):
                try:
                    await engine.close()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    logger.debug("Error closing in-use engine %s: %s", engine_name, exc, exc_info=True)
                self._detach_engine_instance(engine)
        self.pools.clear()
        self.in_use.clear()
        self._notify_waiters()
        self._waiters.clear()

    async def acquire_pair_sorted(
        self,
        a: tuple[str, Path, JsonObject | None, str | None],
        b: tuple[str, Path, JsonObject | None, str | None],
        second_timeout: float | None = None,
    ) -> tuple[AsyncUsiEngine, AsyncUsiEngine]:
        first, second = (a, b) if self._slot_key(a[0], a[3]) <= self._slot_key(b[0], b[3]) else (b, a)

        first_engine = await self.acquire(first[0], first[1], first[2], first[3])
        try:
            if second_timeout is not None:
                second_engine = await asyncio.wait_for(
                    self.acquire(second[0], second[1], second[2], second[3]), timeout=second_timeout
                )
            else:
                second_engine = await self.acquire(second[0], second[1], second[2], second[3])
        except BaseException as exc:
            # Release the first engine on ANY failure, including cancellation (CancelledError is
            # a BaseException), so a cancelled second acquire never leaks the first slot.
            if not isinstance(exc, asyncio.CancelledError):
                logger.debug("Failed to acquire engine pair (%s): %s", second[0], exc, exc_info=True)
            await self.release(first[0], first_engine, first[3])
            raise

        if first is a:
            return (first_engine, second_engine)
        return (second_engine, first_engine)


__all__ = [
    "EnginePool",
]
