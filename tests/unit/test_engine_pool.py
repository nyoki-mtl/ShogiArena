import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from shogiarena._core.contexts.game_session.adapters.engine.pool import EnginePool
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService


class _DummyEngine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_running = True
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.is_running = False


def _write_dummy_config(tmp_path: Path) -> Path:
    path = tmp_path / "dummy_engine.yaml"
    path.write_text("name: dummy\nengine_path: /bin/true\n", encoding="utf-8")
    return path


def _local_instance_pool(max_engines: int) -> InstancePool:
    pool = InstancePool()
    instance = pool.ensure_local_instance()
    instance.config.max_engines = max_engines
    return pool


def _make_engine_factory_service(created: list[_DummyEngine]) -> EngineFactoryService:
    async def _create_engine(*args: Any, **kwargs: Any) -> _DummyEngine:  # noqa: ARG001
        engine = _DummyEngine(name=f"dummy-{len(created)}")
        created.append(engine)
        return engine

    mock_factory = AsyncMock()
    mock_factory.create_engine = _create_engine
    return EngineFactoryService(factory=mock_factory)


def test_engine_pool_slot_key():
    assert EnginePool.slot_key("engine", None) == "engine@auto"
    assert EnginePool.slot_key("engine", "local") == "engine@local"
    assert EnginePool.slot_key("engine", "  local ") == "engine@local"
    assert EnginePool.slot_key("engine", "") == "engine@auto"


@pytest.mark.asyncio
async def test_engine_pool_wakes_waiters_across_slot_keys(tmp_path: Path) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    created: list[_DummyEngine] = []
    service = _make_engine_factory_service(created)
    engine_pool = EnginePool(
        max_instances_per_engine=2,
        instance_pool=instance_pool,
        engine_factory_service=service,
    )

    first = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")
    waiter = asyncio.create_task(
        engine_pool.acquire("engine-b#black", config_path, instance_override="local"),
    )
    await asyncio.sleep(0)
    assert not waiter.done()

    first.is_running = False
    await engine_pool.release("engine-a#black", first, instance_override="local")

    second = await asyncio.wait_for(waiter, timeout=1.0)
    assert second is not first
    assert instance_pool.ensure_local_instance().metrics.engine_processes == 1

    await engine_pool.release("engine-b#black", second, instance_override="local")
    await engine_pool.shutdown_all()


@pytest.mark.asyncio
async def test_engine_pool_evicts_idle_engines_when_instance_capacity_is_full(
    tmp_path: Path,
) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    created: list[_DummyEngine] = []
    service = _make_engine_factory_service(created)
    engine_pool = EnginePool(
        max_instances_per_engine=2,
        instance_pool=instance_pool,
        engine_factory_service=service,
    )

    local_instance = instance_pool.ensure_local_instance()
    first = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")
    await engine_pool.release("engine-a#black", first, instance_override="local")
    assert local_instance.metrics.engine_processes == 1

    second = await asyncio.wait_for(
        engine_pool.acquire("engine-b#black", config_path, instance_override="local"),
        timeout=1.0,
    )
    assert second is not first
    assert first.close_calls >= 1
    assert local_instance.metrics.engine_processes == 1

    await engine_pool.release("engine-b#black", second, instance_override="local")
    await engine_pool.shutdown_all()
