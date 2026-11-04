from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from shogiarena._core.contexts.spsa.adapters.ledger_live_writer import SpsaLiveLedgerWriter
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from tests.unit.arena.services.test_spsa_ledger_projection import _commit_update, _runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_live_writer_drains_repeated_cancel_and_shared_close(tmp_path, monkeypatch, fails):
    source, runtime = _runtime()
    _commit_update(source, runtime)
    path = tmp_path / "spsa" / "ledger.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as destination:
        source.backup(destination)
    source.close()
    session = open_spsa_ledger(tmp_path, use_write_ahead_logging=True)
    writer = SpsaLiveLedgerWriter(run_dir=tmp_path, run_id="run-1")
    entered, release = threading.Event(), threading.Event()
    original = SpsaLedgerRuntime.record_variant_quarantine
    threads = []

    def save(self, **kwargs):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        if fails:
            raise OSError("ledger disk failure")
        return original(self, **kwargs)

    monkeypatch.setattr(SpsaLedgerRuntime, "record_variant_quarantine", save)
    task = asyncio.create_task(
        writer.record_variant_quarantine(
            update_idx=1, pair_id="spsa-u000001-p000000", variant_id="v1", failure_classification="test"
        )
    )
    closes = []
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        for _ in range(2):
            task.cancel("stop")
            await asyncio.sleep(0)
        closes = [asyncio.create_task(writer.close()), asyncio.create_task(writer.close())]
        await asyncio.sleep(0.02)
        assert not task.done() and not any(close.done() for close in closes)
    finally:
        release.set()
        with pytest.raises(OSError if fails else asyncio.CancelledError):
            await task
        await asyncio.gather(*closes)
        session.close()
    assert threads and threads[0] != threading.get_ident()
    await writer.close()


@pytest.mark.asyncio
async def test_live_writer_rejects_new_operations_after_close(tmp_path):
    writer = SpsaLiveLedgerWriter(run_dir=tmp_path, run_id="run")
    await writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        await writer.record_variant_quarantine(
            update_idx=1, pair_id="pair", variant_id="v1", failure_classification="test"
        )
