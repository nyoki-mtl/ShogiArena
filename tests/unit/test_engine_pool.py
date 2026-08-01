import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from shogiarena._core.contexts.game_session.adapters.engine.pool import EnginePool
from shogiarena._core.contexts.instances.application.instance_models import InstanceConfig, InstanceType
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
async def test_engine_pool_spec_acquisition_uses_mapping_factory_only() -> None:
    mappings: list[object] = []

    async def _create_from_mapping(config_mapping: object, **_kwargs: Any) -> _DummyEngine:
        mappings.append(config_mapping)
        return _DummyEngine(name="from-spec")

    async def _legacy_create(*_args: Any, **_kwargs: Any) -> _DummyEngine:
        raise AssertionError("legacy YAML engine creation must not be used")

    mock_factory = AsyncMock()
    mock_factory.create_engine = _legacy_create
    mock_factory.create_engine_from_mapping = _create_from_mapping
    engine_pool = EnginePool(engine_factory_service=EngineFactoryService(factory=mock_factory))
    mapping = {"engine_path": "engine", "working_directory": ".", "options": {}}

    engine = await engine_pool.acquire_from_mapping(
        "engine-a",
        mapping,
        contract_digest="a" * 64,
    )

    assert mappings == [mapping]
    await engine_pool.release("engine-a", engine, contract_digest="a" * 64)
    await engine_pool.shutdown_all()


@pytest.mark.asyncio
async def test_engine_pool_spec_acquisition_passes_configured_instance_to_factory() -> None:
    factory_instance_ids: list[str | None] = []

    async def _create_from_mapping(_mapping: object, **kwargs: Any) -> _DummyEngine:
        factory_instance_ids.append(kwargs["instance_id"])
        return _DummyEngine(name="remote")

    mock_factory = AsyncMock()
    mock_factory.create_engine_from_mapping = _create_from_mapping
    instance_pool = InstancePool()
    instance_pool.add_instance(
        InstanceConfig(
            name="ssh-a",
            type=InstanceType.SSH,
            engine_dir="~/arena/data/engines",
            host="example.invalid",
            slots=2,
            max_engines=2,
        )
    )
    config = SimpleNamespace(
        instance_id="ssh-a",
        cpu_affinity=None,
        handshake_timeout=None,
        go_options={},
    )
    engine_pool = EnginePool(
        engine_factory_service=EngineFactoryService(factory=mock_factory),
        engine_configs={"engine-a": config},
        instance_pool=instance_pool,
    )

    engine = await engine_pool.acquire_from_mapping(
        "engine-a",
        {"engine_path": "engine", "working_directory": ".", "options": {}},
        contract_digest="a" * 64,
    )

    assert factory_instance_ids == ["ssh-a"]
    assert instance_pool.get_instance("ssh-a").metrics.engine_processes == 1  # type: ignore[union-attr]
    await engine_pool.release("engine-a", engine, contract_digest="a" * 64)


@pytest.mark.asyncio
async def test_engine_pool_passes_engine_go_options_to_factory(tmp_path: Path) -> None:
    config_path = _write_dummy_config(tmp_path)
    create_kwargs: list[dict[str, Any]] = []

    async def _create_engine(*args: Any, **kwargs: Any) -> _DummyEngine:  # noqa: ARG001
        create_kwargs.append(kwargs)
        return _DummyEngine(name="dummy")

    mock_factory = AsyncMock()
    mock_factory.create_engine = _create_engine
    service = EngineFactoryService(factory=mock_factory)
    engine_pool = EnginePool(
        engine_factory_service=service,
        engine_configs={
            "engine-a": SimpleNamespace(
                instance_id=None,
                cpu_affinity=None,
                handshake_timeout=None,
                go_options={"nodes": 1000},
            )
        },
    )

    engine = await engine_pool.acquire("engine-a#black", config_path)

    assert create_kwargs[0]["go_options"] == {"nodes": 1000}
    await engine_pool.release("engine-a#black", engine)
    await engine_pool.shutdown_all()


@pytest.mark.asyncio
async def test_engine_pool_reserves_instance_capacity_while_engine_is_starting(tmp_path: Path) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    local_instance = instance_pool.ensure_local_instance()
    create_started = asyncio.Event()
    release_create = asyncio.Event()
    created: list[_DummyEngine] = []
    create_calls = 0

    async def _create_engine(*args: Any, **kwargs: Any) -> _DummyEngine:  # noqa: ARG001
        nonlocal create_calls
        create_calls += 1
        if create_calls == 1:
            create_started.set()
            await release_create.wait()
        engine = _DummyEngine(name=f"dummy-{len(created)}")
        created.append(engine)
        return engine

    mock_factory = AsyncMock()
    mock_factory.create_engine = _create_engine
    service = EngineFactoryService(factory=mock_factory)
    engine_pool = EnginePool(
        max_instances_per_engine=2,
        instance_pool=instance_pool,
        engine_factory_service=service,
    )

    first_task = asyncio.create_task(
        engine_pool.acquire("engine-a#black", config_path, instance_override="local"),
    )
    await asyncio.wait_for(create_started.wait(), timeout=1.0)

    second_task = asyncio.create_task(
        engine_pool.acquire("engine-b#black", config_path, instance_override="local"),
    )
    await asyncio.sleep(0.05)

    assert create_calls == 1
    assert local_instance.metrics.engine_processes == 1

    release_create.set()
    first = await asyncio.wait_for(first_task, timeout=1.0)
    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task

    await engine_pool.release("engine-a#black", first, instance_override="local")
    await engine_pool.shutdown_all()
    assert local_instance.metrics.engine_processes == 0


@pytest.mark.asyncio
async def test_engine_pool_rolls_back_instance_capacity_when_creation_is_cancelled(tmp_path: Path) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    local_instance = instance_pool.ensure_local_instance()
    create_started = asyncio.Event()
    never_release = asyncio.Event()

    async def _create_engine(*args: Any, **kwargs: Any) -> _DummyEngine:  # noqa: ARG001
        create_started.set()
        await never_release.wait()
        raise AssertionError("unreachable")

    mock_factory = AsyncMock()
    mock_factory.create_engine = _create_engine
    service = EngineFactoryService(factory=mock_factory)
    engine_pool = EnginePool(
        max_instances_per_engine=2,
        instance_pool=instance_pool,
        engine_factory_service=service,
    )

    task = asyncio.create_task(engine_pool.acquire("engine-a#black", config_path, instance_override="local"))
    await asyncio.wait_for(create_started.wait(), timeout=1.0)
    assert local_instance.metrics.engine_processes == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert local_instance.metrics.engine_processes == 0
    await engine_pool.shutdown_all()


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
async def test_engine_pool_reuses_released_engine_by_default(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    created: list[_DummyEngine] = []
    service = _make_engine_factory_service(created)
    engine_pool = EnginePool(
        max_instances_per_engine=1,
        instance_pool=instance_pool,
        engine_factory_service=service,
    )

    first = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")
    with caplog.at_level(logging.DEBUG):
        await engine_pool.release("engine-a#black", first, instance_override="local")
    second = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")

    assert second is first
    assert created == [first]
    assert first.close_calls == 0
    assert "Returned engine to pool for engine-a#black@local (active=0 idle=1 lifecycle=reuse)" in caplog.text

    await engine_pool.release("engine-a#black", second, instance_override="local")
    await engine_pool.shutdown_all()


@pytest.mark.asyncio
async def test_engine_pool_per_game_closes_released_engine(tmp_path: Path) -> None:
    config_path = _write_dummy_config(tmp_path)
    instance_pool = _local_instance_pool(max_engines=1)
    local_instance = instance_pool.ensure_local_instance()
    created: list[_DummyEngine] = []
    service = _make_engine_factory_service(created)
    engine_pool = EnginePool(
        max_instances_per_engine=1,
        instance_pool=instance_pool,
        engine_factory_service=service,
        lifecycle_policy="per_game",
    )

    first = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")
    await engine_pool.release("engine-a#black", first, instance_override="local")

    assert first.close_calls == 1
    assert engine_pool.pools["engine-a#black@local"] == []
    assert local_instance.metrics.engine_processes == 0

    second = await engine_pool.acquire("engine-a#black", config_path, instance_override="local")
    assert second is not first
    assert created == [first, second]

    await engine_pool.release("engine-a#black", second, instance_override="local")
    await engine_pool.shutdown_all()


@pytest.mark.asyncio
async def test_acquire_pair_sorted_releases_first_on_cancel(tmp_path: Path) -> None:
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

    a = ("engine-a#black", config_path, None, "local")
    b = ("engine-b#black", config_path, None, "local")
    # The first acquire takes the only slot; the second blocks on capacity. Cancelling the pair
    # acquire must release the first engine instead of leaking the slot (regression for the
    # except clause that did not catch CancelledError).
    task = asyncio.create_task(engine_pool.acquire_pair_sorted(a, b))
    await asyncio.sleep(0.05)
    assert local_instance.metrics.engine_processes == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The first engine is back in the pool, so a fresh acquire succeeds instead of timing out
    # on a leaked in-use slot.
    reacquired = await asyncio.wait_for(
        engine_pool.acquire("engine-a#black", config_path, instance_override="local"),
        timeout=1.0,
    )
    assert reacquired is not None
    await engine_pool.release("engine-a#black", reacquired, instance_override="local")
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
