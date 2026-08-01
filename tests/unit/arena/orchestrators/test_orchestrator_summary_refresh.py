from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import BaseOrchestrator
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.shared.kernel.session_hooks import NoopGameLifecycleHooks


class _StubOrchestrator(BaseOrchestrator):
    async def run(self) -> None:
        return None


def _build_orchestrator(tmp_path: Path, summary_updater: object | None) -> _StubOrchestrator:
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="run-001")
    return _StubOrchestrator(
        session_context=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        summary_updater=summary_updater,  # type: ignore[arg-type]
    )


async def _drain() -> None:
    """Let scheduled summary refresh tasks run to completion."""

    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_unchanged_engine_options_do_not_trigger_summary_refresh(tmp_path: Path) -> None:
    calls = 0

    async def updater() -> None:
        nonlocal calls
        calls += 1

    orchestrator = _build_orchestrator(tmp_path, updater)

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    await _drain()
    assert calls == 1

    # Engines re-announce the same option set on every handshake; that must not re-run the summary.
    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    await _drain()
    assert calls == 1

    orchestrator._handle_engine_options("engine-a", {"Threads": 2}, None)  # noqa: SLF001
    await _drain()
    assert calls == 2


@pytest.mark.asyncio
async def test_changed_engine_info_triggers_summary_refresh(tmp_path: Path) -> None:
    calls = 0

    async def updater() -> None:
        nonlocal calls
        calls += 1

    orchestrator = _build_orchestrator(tmp_path, updater)

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, {"name": "a"})  # noqa: SLF001
    await _drain()
    assert calls == 1

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, {"name": "b"})  # noqa: SLF001
    await _drain()
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_option_changes_coalesce_into_one_follow_up(tmp_path: Path) -> None:
    calls = 0
    release = asyncio.Event()

    async def updater() -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    orchestrator = _build_orchestrator(tmp_path, updater)

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    await _drain()
    assert calls == 1

    # Three further changes arrive while the first refresh is still running. They must collapse
    # into a single follow-up rather than stacking one refresh per callback.
    for threads in (2, 3, 4):
        orchestrator._handle_engine_options("engine-a", {"Threads": threads}, None)  # noqa: SLF001
    await _drain()
    assert calls == 1

    release.set()
    await _drain()
    assert calls == 2

    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_no_summary_updater_schedules_nothing(tmp_path: Path) -> None:
    orchestrator = _build_orchestrator(tmp_path, None)

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    await _drain()

    assert orchestrator._summary_refresh_task is None  # noqa: SLF001
    assert orchestrator.get_engine_option_snapshots() == {"engine-a": {"Threads": 1}}


def test_progress_sink_is_detached_without_a_consumer(tmp_path: Path) -> None:
    orchestrator = _build_orchestrator(tmp_path, None)
    rules = SimpleNamespace(
        time_control=None,
        adjudication=SimpleNamespace(
            resign_threshold_cp=None,
            is_max_plies_enabled=False,
            max_plies=None,
            resign_move_count=3,
            is_resign_two_sided=True,
        ),
        repetition_occurrences_to_draw=4,
    )

    orchestrator._initialize_common_components(  # noqa: SLF001
        num_workers=2,
        engines=[],
        rules=rules,
    )

    assert orchestrator.api_server is None
    assert orchestrator.progress_sink is None
    assert orchestrator._active_game_runners == set()  # noqa: SLF001


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_summary_refresh(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def updater() -> None:
        await release.wait()

    orchestrator = _build_orchestrator(tmp_path, updater)

    orchestrator._handle_engine_options("engine-a", {"Threads": 1}, None)  # noqa: SLF001
    await _drain()

    await orchestrator.shutdown()

    assert orchestrator._summary_refresh_task is None  # noqa: SLF001
