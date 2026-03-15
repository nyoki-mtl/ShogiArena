from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
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
        "engine-a": SimpleNamespace(instance_id=None, cpu_affinity=None, handshake_timeout=None),
    }

    pool = orchestrator.create_engine_pool(3)

    assert not hasattr(orchestrator, "config")
    assert pool.max_instances == 3
    assert pool._default_handshake_timeout == 12.5  # noqa: SLF001
