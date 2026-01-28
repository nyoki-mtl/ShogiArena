"""Engine pool implementation for orchestrators."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.instances.pool import InstancePool

logger = logging.getLogger(__name__)


class EnginePool:
    """Manage reusable ``AsyncUsiEngine`` instances with bounded parallelism."""

    def __init__(
        self,
        max_instances_per_engine: int = 2,
        *,
        engine_configs: Mapping[str, Any] | None = None,
        instance_pool: InstancePool | None = None,
    ) -> None:
        self.max_instances = max_instances_per_engine
        self.pools: dict[str, list[AsyncUsiEngine]] = {}
        self.in_use: dict[str, set[AsyncUsiEngine]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._wait_queues: dict[str, asyncio.Queue[asyncio.Event]] = {}
        self._engine_configs: Mapping[str, Any] = engine_configs or {}
        self._instance_pool = instance_pool
        self._engine_instance_ids: dict[AsyncUsiEngine, str] = {}

    def _resolve_instance_id(self, engine_name: str, instance_override: str | None) -> str | None:
        if instance_override is not None:
            return instance_override
        base_name = engine_name.split("#", 1)[0]
        spec = self._engine_configs.get(base_name)
        instance_id = getattr(spec, "instance_id", None) if spec is not None else None
        return instance_id

    def _resolve_instance(self, engine_name: str, instance_override: str | None) -> Any | None:
        if self._instance_pool is None:
            return None
        instance_id = self._resolve_instance_id(engine_name, instance_override)
        if instance_id:
            instance = self._instance_pool.get_instance(instance_id)
            if instance is None and instance_id == "local":
                instance = self._instance_pool.ensure_local_instance()
            return instance
        return self._instance_pool.ensure_local_instance()

    @staticmethod
    def slot_key(engine_key: str, instance_override: str | None) -> str:
        """Return the internal pool slot key for a given engine/instance pair."""
        override = (instance_override or "auto").strip()
        return f"{engine_key}@{override}" if override else f"{engine_key}@auto"

    @staticmethod
    def _slot_key(engine_key: str, instance_override: str | None) -> str:
        return EnginePool.slot_key(engine_key, instance_override)

    async def acquire(
        self,
        engine_name: str,
        config_path: Path,
        extra_options: dict[str, Any] | None = None,
        instance_override: str | None = None,
    ) -> AsyncUsiEngine:
        slot_key = self._slot_key(engine_name, instance_override)
        if slot_key not in self._locks:
            self._locks[slot_key] = asyncio.Lock()
            self._wait_queues[slot_key] = asyncio.Queue()

        while True:
            async with self._locks[slot_key]:
                pool = self.pools.setdefault(slot_key, [])
                in_use = self.in_use.setdefault(slot_key, set())

                while pool:
                    engine = pool.pop()
                    if engine.is_running:
                        in_use.add(engine)
                        logger.debug("Reused engine %s from pool", slot_key)
                        return engine
                    try:
                        await engine.close()
                    except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                        logger.debug(
                            "Discarded closed engine %s during acquire: %s",
                            slot_key,
                            exc,
                            exc_info=True,
                        )
                    instance_id = self._engine_instance_ids.pop(engine, None)
                    if instance_id and self._instance_pool:
                        instance = self._instance_pool.get_instance(instance_id)
                        if instance is None and instance_id == "local":
                            instance = self._instance_pool.ensure_local_instance()
                        if instance is not None:
                            instance.remove_engine_processes(1)

                total_instances = len(pool) + len(in_use)
                if total_instances < self.max_instances:
                    logger.debug("Creating new engine instance for %s", slot_key)
                    base_name = engine_name.split("#", 1)[0]
                    spec = self._engine_configs.get(base_name)
                    instance_id = getattr(spec, "instance_id", None) if spec is not None else None
                    affinity = getattr(spec, "cpu_affinity", None) if spec is not None else None
                    if instance_override is not None:
                        instance_id = instance_override
                    instance = self._resolve_instance(engine_name, instance_override)
                    capacity_blocked = False
                    if instance is not None:
                        capacity = instance.max_engine_capacity
                        if instance.metrics.engine_processes >= capacity:
                            logger.debug(
                                "Instance %s at engine capacity (%s); waiting...",
                                instance.name,
                                capacity,
                            )
                            capacity_blocked = True

                    if not capacity_blocked:
                        engine = await EngineFactory.create_engine(
                            config_path,
                            timeout=30.0,
                            extra_options=extra_options,
                            engine_name=engine_name,
                            instance_id=instance_id,
                            instance_pool=self._instance_pool,
                            cpu_affinity=affinity,
                        )
                        if instance is not None:
                            self._engine_instance_ids[engine] = instance.name
                            instance.add_engine_processes(1)
                        in_use.add(engine)
                        return engine

            logger.debug("Pool for %s at capacity (%s), waiting...", slot_key, self.max_instances)
            wait_event = asyncio.Event()
            await self._wait_queues[slot_key].put(wait_event)
            await wait_event.wait()

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

            if len(pool) < self.max_instances and engine.is_running:
                pool.append(engine)
                logger.debug("Returned engine %s to pool", slot_key)
            else:
                try:
                    await engine.close()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.debug("Error closing engine %s during release: %s", slot_key, exc, exc_info=True)
                logger.debug("Terminated excess engine instance for %s", slot_key)
                instance_id = self._engine_instance_ids.pop(engine, None)
                if instance_id and self._instance_pool:
                    instance = self._instance_pool.get_instance(instance_id)
                    if instance is None and instance_id == "local":
                        instance = self._instance_pool.ensure_local_instance()
                    if instance is not None:
                        instance.remove_engine_processes(1)

            queue = self._wait_queues.get(slot_key)
            if queue and not queue.empty():
                wait_event = await queue.get()
                wait_event.set()
                logger.debug("Signaled waiting request for %s", slot_key)

    async def shutdown_all(self) -> None:
        logger.debug("Shutting down all engines")
        for engine_name, pool in list(self.pools.items()):
            logger.debug("Shutting down pool for %s", engine_name)
            for engine in list(pool):
                try:
                    await engine.close()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.debug("Error closing pooled engine %s: %s", engine_name, exc, exc_info=True)
                instance_id = self._engine_instance_ids.pop(engine, None)
                if instance_id and self._instance_pool:
                    instance = self._instance_pool.get_instance(instance_id)
                    if instance is None and instance_id == "local":
                        instance = self._instance_pool.ensure_local_instance()
                    if instance is not None:
                        instance.remove_engine_processes(1)
            for engine in list(self.in_use.get(engine_name, set())):
                try:
                    await engine.close()
                except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                    logger.debug("Error closing in-use engine %s: %s", engine_name, exc, exc_info=True)
                instance_id = self._engine_instance_ids.pop(engine, None)
                if instance_id and self._instance_pool:
                    instance = self._instance_pool.get_instance(instance_id)
                    if instance is None and instance_id == "local":
                        instance = self._instance_pool.ensure_local_instance()
                    if instance is not None:
                        instance.remove_engine_processes(1)
        self.pools.clear()
        self.in_use.clear()

    async def acquire_pair_sorted(
        self,
        a: tuple[str, Path, dict[str, Any] | None, str | None],
        b: tuple[str, Path, dict[str, Any] | None, str | None],
        second_timeout: float | None = None,
    ) -> tuple[AsyncUsiEngine, AsyncUsiEngine]:
        """Acquire two engines with deadlock-safe ordering."""
        first, second = (a, b) if self._slot_key(a[0], a[3]) <= self._slot_key(b[0], b[3]) else (b, a)

        first_engine = await self.acquire(first[0], first[1], first[2], first[3])
        try:
            if second_timeout is not None:
                second_engine = await asyncio.wait_for(
                    self.acquire(second[0], second[1], second[2], second[3]), timeout=second_timeout
                )
            else:
                second_engine = await self.acquire(second[0], second[1], second[2], second[3])
        except (asyncio.TimeoutError, OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to acquire engine pair (%s): %s", second[0], exc, exc_info=True)
            await self.release(first[0], first_engine, first[3])
            raise

        if first is a:
            return (first_engine, second_engine)
        return (second_engine, first_engine)
