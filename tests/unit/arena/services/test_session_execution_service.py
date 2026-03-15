from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.session.execution_service import (
    TournamentSessionExecutionService,
)


class _ResultBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object | None]] = []

    def build_tournament_run_result(self, results: object, sprt_status: object | None) -> SimpleNamespace:
        self.calls.append((results, sprt_status))
        return SimpleNamespace(run_id="run-001")


class _ProgressStub:
    def __init__(self) -> None:
        self.finalized_payloads: list[object] = []

    def finalize(self, payload: object) -> None:
        self.finalized_payloads.append(payload)


class _ReplacementProgressStub(_ProgressStub):
    pass


class _RunnerStub:
    def __init__(self) -> None:
        self.session_phase = "running"
        self._progress = _ProgressStub()

    @property
    def progress(self) -> _ProgressStub:
        return self._progress

    def set_progress(self, reporter: object) -> None:
        self._progress = reporter  # type: ignore[assignment]

    def are_services_closed(self) -> bool:
        return False

    def get_sprt_status(self) -> dict[str, str]:
        return {"decision": "accept_h1"}

    async def calculate_results(self) -> object:
        return {"wins": 3}

    async def finalize_tournament(self, _results: object) -> None:
        return None

    async def stop_services(self) -> None:
        return None


def test_prepare_progress_swaps_reporter_and_restore_recovers_previous() -> None:
    service = TournamentSessionExecutionService()
    runner = _RunnerStub()
    replacement = _ReplacementProgressStub()

    previous = service.prepare_progress(cast(Any, runner), progress_reporter=cast(Any, replacement))

    assert previous is not replacement
    assert runner.progress is replacement

    service.restore_progress(cast(Any, runner), previous=cast(Any, previous))

    assert runner.progress is previous


@pytest.mark.asyncio
async def test_finalize_session_uses_get_sprt_status_without_runner_private_attr() -> None:
    builder = _ResultBuilder()
    runner = _RunnerStub()

    result = await TournamentSessionExecutionService().finalize_session(
        cast(Any, runner),
        result_builder=cast(Any, builder),
    )

    assert not hasattr(runner, "_sprt")
    assert result is not None
    assert result.run_id == "run-001"
    assert builder.calls == [({"wins": 3}, {"decision": "accept_h1"})]
    assert runner.session_phase == "finished"
    assert runner.progress.finalized_payloads == [{"status": "finished", "run_id": "run-001"}]
