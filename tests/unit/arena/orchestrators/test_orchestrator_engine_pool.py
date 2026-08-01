from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.instances.application.health_checker import HealthChecker
from shogiarena._core.contexts.instances.application.instance_models import (
    InstanceConfig,
    InstanceMetrics,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.shared.kernel.session_hooks import NoopGameLifecycleHooks


class _StubOrchestrator(BaseOrchestrator):
    async def run(self) -> None:
        return None


def test_create_engine_pool_uses_explicit_default_handshake_timeout(tmp_path: Path) -> None:
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="run-001")
    orchestrator = _StubOrchestrator(
        session_context=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        default_engine_handshake_timeout=12.5,
    )
    orchestrator.engine_configs = {
        "engine-a": SimpleNamespace(instance_id=None, cpu_affinity=None, handshake_timeout=None, go_options={}),
    }

    pool = orchestrator.create_engine_pool(3)

    assert not hasattr(orchestrator, "config")
    assert pool.max_instances == 3
    assert pool._default_handshake_timeout == 12.5  # noqa: SLF001


def test_create_engine_pool_passes_engine_lifecycle(tmp_path: Path) -> None:
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="run-001")
    orchestrator = _StubOrchestrator(
        session_context=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        engine_lifecycle="per_game",
    )

    pool = orchestrator.create_engine_pool(3)

    assert pool.lifecycle_policy == "per_game"


@pytest.mark.asyncio
async def test_instance_health_preflight_runs_without_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = InstancePool()
    instance = pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=4,
        )
    )
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(
        storage=storage,
        num_workers=1,
        instance_pool=pool,
        run_id="run-001",
    )
    orchestrator = _StubOrchestrator(
        session_context=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
    )
    probe = AsyncMock(return_value=InstanceMetrics(is_reachable=True, cpu_count=8))
    monkeypatch.setattr(HealthChecker, "check_instance_health", probe)

    await orchestrator.preflight_instance_health()

    probe.assert_awaited_once_with(instance)
    assert instance.metrics.cpu_count == 8
