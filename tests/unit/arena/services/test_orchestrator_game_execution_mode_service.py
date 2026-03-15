from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.game_execution_mode_service import (
    OrchestratorExecutionModeRequest,
    OrchestratorGameExecutionModeService,
)


@pytest.mark.asyncio
async def test_execute_runs_remote_after_install_when_required() -> None:
    service = OrchestratorGameExecutionModeService()
    calls: list[str] = []

    async def _ensure(ids: set[str]) -> None:
        calls.append(f"ensure:{sorted(ids)}")

    def _mark_complete() -> None:
        calls.append("mark")

    async def _run_remote() -> str:
        calls.append("remote")
        return "remote-result"

    async def _run_local() -> str:
        calls.append("local")
        return "local-result"

    result = await service.execute(
        request=OrchestratorExecutionModeRequest(
            should_require_install=True,
            black_instance_id="inst-a",
            white_instance_id="inst-a",
            selected_remote_instance=object(),
        ),
        ensure_remote_install=_ensure,
        mark_install_complete=_mark_complete,
        run_remote=_run_remote,
        run_local=_run_local,
    )

    assert result == "remote-result"
    assert calls == ["ensure:['inst-a']", "mark", "remote"]


@pytest.mark.asyncio
async def test_execute_filters_local_and_none_from_install_targets() -> None:
    service = OrchestratorGameExecutionModeService()
    calls: list[str] = []

    async def _ensure(ids: set[str]) -> None:
        calls.append(f"ensure:{sorted(ids)}")

    async def _run_remote() -> str:
        calls.append("remote")
        return "remote-result"

    async def _run_local() -> str:
        calls.append("local")
        return "local-result"

    result = await service.execute(
        request=OrchestratorExecutionModeRequest(
            should_require_install=True,
            black_instance_id="local",
            white_instance_id=None,
            selected_remote_instance=None,
        ),
        ensure_remote_install=_ensure,
        mark_install_complete=lambda: calls.append("mark"),
        run_remote=_run_remote,
        run_local=_run_local,
    )

    assert result == "local-result"
    assert calls == ["mark", "local"]


@pytest.mark.asyncio
async def test_execute_runs_local_when_remote_not_selected() -> None:
    service = OrchestratorGameExecutionModeService()
    calls: list[str] = []

    async def _run_remote() -> str:
        calls.append("remote")
        return "remote-result"

    async def _run_local() -> str:
        calls.append("local")
        return "local-result"

    result = await service.execute(
        request=OrchestratorExecutionModeRequest(
            should_require_install=False,
            black_instance_id=None,
            white_instance_id=None,
            selected_remote_instance=None,
        ),
        run_remote=_run_remote,
        run_local=_run_local,
    )

    assert result == "local-result"
    assert calls == ["local"]


@pytest.mark.asyncio
async def test_execute_raises_when_install_callbacks_are_missing() -> None:
    service = OrchestratorGameExecutionModeService()

    async def _run_remote() -> str:
        return "remote-result"

    async def _run_local() -> str:
        return "local-result"

    with pytest.raises(ValueError, match="Install callbacks"):
        await service.execute(
            request=OrchestratorExecutionModeRequest(
                should_require_install=True,
                black_instance_id="inst-a",
                white_instance_id="inst-b",
                selected_remote_instance=None,
            ),
            run_remote=_run_remote,
            run_local=_run_local,
        )
