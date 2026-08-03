"""Guard the SPSA runner wiring that keeps derived JSON projection off the event loop.

`project_spsa_ledger` rebuilds the compatibility views from the whole run, so the
per-game path must never call it inline. These tests drive the production runner
methods rather than asserting on source text, so a rewiring that reintroduces the
inline projection fails here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from shogiarena._core.contexts.spsa.adapters import runner as runner_module
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState


class _RecordingScheduler:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def request(self) -> None:
        self._calls.append("scheduler-request")

    async def drain(self) -> None:
        self._calls.append("scheduler-drain")

    def close(self) -> None:
        self._calls.append("scheduler-close")


class _LedgerRuntime:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def project_derived_json(self, *, run_dir: Path) -> None:
        del run_dir
        self._calls.append("inline-project")

    def commit_terminal(self, *, status: str, reason: str, resumable: bool) -> None:
        del status, resumable
        self._calls.append(f"terminal:{reason}")

    def terminal_payload(self) -> dict[str, object] | None:
        return None

    def completion_status_payload(self, *, cleanup_error: str | None = None) -> dict[str, object] | None:
        del cleanup_error
        return None


class _Ledger:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def close(self) -> None:
        self._calls.append("ledger-close")


def _completion_event() -> Any:
    payload = SimpleNamespace(update_idx=1, phase="tuning", winner_code=1)
    return SimpleNamespace(game_id="g1", payload=payload)


@pytest.mark.asyncio
async def test_game_completion_schedules_projection_instead_of_running_it_inline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner._state = SpsaRunnerState(
        db_service=SimpleNamespace(),  # type: ignore[arg-type]
        ledger_runtime=_LedgerRuntime(calls),  # type: ignore[arg-type]
    )
    runner._state.completion_lock = asyncio.Lock()
    runner._derived_json_scheduler = _RecordingScheduler(calls)  # type: ignore[assignment]
    runner.set_progress(
        SimpleNamespace(on_game_complete=lambda _payload: calls.append("progress"))  # type: ignore[arg-type]
    )

    monkeypatch.setattr(runner_module, "persist_spsa_game_completion", lambda **_kwargs: True)

    event = _completion_event()
    await runner._handle_game_completion(event, event.payload)

    assert "inline-project" not in calls, "the per-game path must not rebuild the whole run inline"
    assert calls == ["scheduler-request", "progress"]


@pytest.mark.asyncio
async def test_teardown_drains_scheduler_before_the_terminal_projection(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner._storage = SimpleNamespace(run_dir=tmp_path)
    runner._state = SpsaRunnerState(
        ledger=_Ledger(calls),  # type: ignore[arg-type]
        ledger_runtime=_LedgerRuntime(calls),  # type: ignore[arg-type]
        terminal_status="clean",
        terminal_reason="completed",
        terminal_resumable=False,
    )
    runner._derived_json_scheduler = _RecordingScheduler(calls)  # type: ignore[assignment]

    await runner._stop_additional_services()

    # A background projection carrying pre-terminal state must not land after the
    # terminal one, so the drain has to precede the commit.
    assert calls.index("scheduler-drain") < calls.index("terminal:completed")
    assert calls.index("terminal:completed") < calls.index("inline-project")
    assert calls.index("inline-project") < calls.index("ledger-close")
