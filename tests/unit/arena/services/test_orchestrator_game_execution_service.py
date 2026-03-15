from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorExecutionModeRequest,
    OrchestratorGameExecutionModeService,
)
from shogiarena._core.contexts.game_session.application.orchestration.game_execution_service import (
    OrchestratorGameExecutionRequest,
    OrchestratorGameExecutionService,
)
from shogiarena._core.contexts.game_session.application.orchestration.local_execution_spec_service import (
    LocalExecutionSpecRequest,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def _limits() -> TimeControlLimits:
    return TimeControlLimits(time_ms=10_000, increment_ms=100)


@pytest.mark.asyncio
async def test_execute_runs_remote_with_resolved_instance() -> None:
    service = OrchestratorGameExecutionService(
        execution_mode_service=OrchestratorGameExecutionModeService(),
    )
    calls: list[str] = []
    remote_instance = object()

    async def _run_remote_with_instance(instance: object) -> str:
        calls.append(f"remote:{instance is remote_instance}")
        return "remote-result"

    async def _execute_local(_spec: object) -> str:
        calls.append("local")
        return "local-result"

    async def _ensure(ids: set[str]) -> None:
        calls.append(f"ensure:{sorted(ids)}")

    def _mark_install_complete() -> None:
        calls.append("mark")

    result = await service.execute(
        request=OrchestratorGameExecutionRequest(
            execution_mode=OrchestratorExecutionModeRequest(
                should_require_install=True,
                black_instance_id="inst-a",
                white_instance_id="inst-a",
                selected_remote_instance=remote_instance,
            ),
            local_execution=LocalExecutionSpecRequest(
                black_item="black",
                white_item="white",
                initial_sfen="startpos",
                game_id="g1",
                black_limits=_limits(),
                white_limits=_limits(),
            ),
        ),
        game_execution_spec_factory=lambda **kwargs: kwargs,
        run_remote_with_instance=_run_remote_with_instance,
        execute_local=_execute_local,
        ensure_remote_install=_ensure,
        mark_install_complete=_mark_install_complete,
    )

    assert result == "remote-result"
    assert calls == ["ensure:['inst-a']", "mark", "remote:True"]


@pytest.mark.asyncio
async def test_execute_builds_local_spec_and_runs_local_path() -> None:
    service = OrchestratorGameExecutionService(
        execution_mode_service=OrchestratorGameExecutionModeService(),
    )
    calls: list[str] = []
    captured_spec: dict[str, object] | None = None

    async def _run_remote_with_instance(_instance: object) -> str:
        calls.append("remote")
        return "remote-result"

    async def _execute_local(spec: dict[str, object]) -> str:
        nonlocal captured_spec
        calls.append("local")
        captured_spec = spec
        return "local-result"

    result = await service.execute(
        request=OrchestratorGameExecutionRequest(
            execution_mode=OrchestratorExecutionModeRequest(
                should_require_install=False,
                black_instance_id=None,
                white_instance_id=None,
                selected_remote_instance=None,
            ),
            local_execution=LocalExecutionSpecRequest(
                black_item="black-item",
                white_item="white-item",
                initial_sfen="startpos",
                game_id="g2",
                black_limits=_limits(),
                white_limits=_limits(),
                game_round=3,
            ),
        ),
        game_execution_spec_factory=lambda **kwargs: kwargs,
        run_remote_with_instance=_run_remote_with_instance,
        execute_local=_execute_local,
    )

    assert result == "local-result"
    assert calls == ["local"]
    assert captured_spec is not None
    assert captured_spec["black_item"] == "black-item"
    assert captured_spec["white_item"] == "white-item"
    assert captured_spec["game_id"] == "g2"
    assert captured_spec["game_round"] == 3
