"""Stdout failure must not interrupt cleanup entered after a successful game."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import threading
from pathlib import Path

import pytest
from test_internal_worker_output_production import _spec_file

from shogiarena._core.contexts.game_session.adapters.orchestration import game_execution_worker
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionSpecPayload,
    seal_game_execution_spec,
)
from shogiarena._core.contexts.instances.application.engine_process_handles import LocalEngineProcess
from shogiarena._core.interfaces.cli.internal import output, remote


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["engines", "watchdog"])
async def test_stdout_failure_during_normal_cleanup_drains_engines_and_watchdog(tmp_path, monkeypatch, stage):
    _, original_spec = _spec_file(tmp_path)
    payload = original_spec.model_dump(mode="json", exclude={"execution_digest"})
    payload["timeout"]["watchdog"] = "required"
    # Invoke Python directly so the captured PIDs are the engines themselves,
    # including on Windows (the regular CLI smoke also covers .bat launchers).
    for role in ("black", "white"):
        process = payload[f"{role}_engine"]["process"]
        process["artifact"]["entrypoint"] = sys.executable
        process["artifact"]["sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        process["arguments"] = ["-I", str(tmp_path / f"{role}.py")]
    spec = seal_game_execution_spec(GameExecutionSpecPayload.model_validate(payload))
    entered = threading.Event()
    release, stop_requested = asyncio.Event(), asyncio.Event()
    engines_done, watchdog_done = asyncio.Event(), asyncio.Event()
    handles, watchdogs, shutdown_args = [], [], []
    original_spawn = LocalEngineProcess.spawn

    async def spawn(cls, *args, **kwargs):
        handle = await original_spawn(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(LocalEngineProcess, "spawn", classmethod(spawn))
    watchdog_class = game_execution_worker.RuntimeWatchdog
    original_watchdog_stop = watchdog_class.stop

    def watchdog():
        instance = watchdog_class()
        watchdogs.append(instance)
        return instance

    monkeypatch.setattr(game_execution_worker, "RuntimeWatchdog", watchdog)

    async def pause():
        assert asyncio.current_task().cancelling() == 0
        entered.set()
        await release.wait()

    original_shutdown = game_execution_worker._shutdown_engines

    async def shutdown(**kwargs):
        shutdown_args.append(kwargs)
        if stage == "engines":
            await pause()
        await original_shutdown(**kwargs)
        engines_done.set()

    async def stop_watchdog(self):
        if stage == "watchdog":
            await pause()
        await original_watchdog_stop(self)
        watchdog_done.set()

    monkeypatch.setattr(game_execution_worker, "_shutdown_engines", shutdown)
    monkeypatch.setattr(watchdog_class, "stop", stop_watchdog)
    original_write = output._write_and_flush
    sink = io.StringIO()
    monkeypatch.setattr(output.sys, "stdout", sink)

    def write(stream, line):
        event = json.loads(line)
        if event.get("type") == "move_progress" and event.get("game_result") is not None:
            assert entered.wait(5)
            raise BrokenPipeError("stdout failed after normal cleanup began")
        original_write(stream, line)

    monkeypatch.setattr(output, "_write_and_flush", write)
    original_stop = remote._stop_execution

    def stop(worker, execution):
        original_stop(worker, execution)
        stop_requested.set()

    monkeypatch.setattr(remote, "_stop_execution", stop)
    owner = asyncio.create_task(remote.run_game_execution_spec(spec, execution_root=tmp_path))
    try:
        await asyncio.wait_for(stop_requested.wait(), 10)
        for _ in range(2):
            owner.cancel("caller cancellation during cleanup")
            await asyncio.sleep(0)
        assert not owner.done(), "normal cleanup was abandoned after stdout failure"
        assert not (engines_done if stage == "engines" else watchdog_done).is_set()
    finally:
        release.set()
        try:
            with pytest.raises(BrokenPipeError, match="stdout failed after normal cleanup began"):
                await owner
        finally:
            # A failing pre-fix regression must not leave real processes or
            # watchdogs behind. These fallbacks do not set the completion flags.
            if not engines_done.is_set():
                for kwargs in shutdown_args:
                    await original_shutdown(**kwargs)
            if not watchdog_done.is_set():
                for instance in watchdogs:
                    await original_watchdog_stop(instance)
    assert engines_done.is_set() and watchdog_done.is_set()
    assert len(handles) == 2
    assert all(handle.returncode is not None and handle._disconnected.done() for handle in handles)
    assert all(instance._loop_task is None and instance._thread is None for instance in watchdogs)
    assert not any("execution_digest" in json.loads(line) for line in sink.getvalue().splitlines())
