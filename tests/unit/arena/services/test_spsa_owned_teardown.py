from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from tests.unit.arena.services.test_spsa_runner_projection_wiring import _Ledger, _LedgerRuntime, _RecordingScheduler


@pytest.mark.asyncio
async def test_cancelled_teardown_finishes_writer_terminal_and_connection_close(tmp_path):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    class Writer:
        async def close(self):
            entered.set()
            await release.wait()
            calls.append("writer-closed")

    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner._storage = SimpleNamespace(run_dir=tmp_path)
    runner._state = SpsaRunnerState(
        ledger=_Ledger(calls),
        ledger_runtime=_LedgerRuntime(calls),
        live_ledger_writer=Writer(),
        terminal_status="clean",
        terminal_reason="completed",
        terminal_resumable=False,
    )
    runner._derived_json_scheduler = _RecordingScheduler(calls)
    task = asyncio.create_task(runner._stop_additional_services())
    await entered.wait()
    for _ in range(2):
        task.cancel("cancel teardown")
        await asyncio.sleep(0)
    assert not task.done() and calls == []
    release.set()
    with pytest.raises(asyncio.CancelledError, match="cancel teardown"):
        await task
    assert calls.index("writer-closed") < calls.index("terminal:completed") < calls.index("ledger-close")
    assert runner._state.ledger is None
