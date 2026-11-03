"""Exited launcher processes must not leave live asyncio pipe transports."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import psutil
import pytest

from shogiarena._core.contexts.instances.application.engine_process_handles import LocalEngineProcess
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge


@pytest.mark.asyncio
async def test_exited_launcher_with_inherited_pipes_is_fully_disconnected_before_stop_returns(tmp_path):
    pid_file = tmp_path / "child.json"
    source = """
import json, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
Path(sys.argv[1]).write_text(json.dumps({'pid': child.pid}), encoding='utf-8')
"""
    process = await LocalEngineProcess.spawn(
        [sys.executable, "-c", source, str(pid_file)],
        cwd=tmp_path,
        env=None,
        limit=65536,
    )
    child = None
    try:
        async with asyncio.timeout(10):
            while process.returncode is None or not pid_file.exists():
                await asyncio.sleep(0.001)
        child = psutil.Process(json.loads(pid_file.read_text(encoding="utf-8"))["pid"])
        child.create_time()  # retain the owned identity for safe cleanup
        assert child.is_running()
        # The inherited stdout/stderr keep their transports open despite exit.
        assert await process.wait() == 0
        assert not process._disconnected.done()
        bridge = SpawnerBackedUSIBridge(instance=SimpleNamespace(name="local", is_ssh=False), engine_path="probe")
        bridge.process = process
        await asyncio.wait_for(bridge.stop_process(), 5)
        assert bridge.process is None
        assert process._disconnected.done()
        assert process._transport.is_closing()
        assert all(process._transport.get_pipe_transport(fd).is_closing() for fd in (0, 1, 2))
    finally:
        await process.close_transport()
        # This test deliberately owns a descendant which outlives the launcher;
        # transport closure is not a claim that arbitrary descendants were reaped.
        if child is not None:
            try:
                child.kill()
                await asyncio.to_thread(child.wait, timeout=5)
            except psutil.NoSuchProcess:
                pass
