"""Health command ownership includes spawn, descendants and pipe teardown."""

from __future__ import annotations

import asyncio
import os
import sys

import psutil
import pytest
import pytest_asyncio

from shogiarena._core.contexts.instances.application import health_checker, health_checker_metrics, local_command
from shogiarena._core.contexts.instances.application.instance_models import InstanceMetrics
from shogiarena._core.contexts.instances.application.local_command import run_local_command


@pytest_asyncio.fixture
async def transports(monkeypatch):
    loop = asyncio.get_running_loop()
    original = loop.subprocess_exec
    captured = []

    async def spawn(factory, *args, **kwargs):
        transport, protocol = await original(factory, *args, **kwargs)
        captured.append((transport, protocol))
        return transport, protocol

    monkeypatch.setattr(loop, "subprocess_exec", spawn)
    return captured


def _assert_closed(transports):
    assert len(transports) == 1
    transport, protocol = transports[0]
    assert transport.get_returncode() is not None
    assert protocol.disconnected.done()
    assert all(transport.get_pipe_transport(fd).is_closing() for fd in (1, 2))


@pytest.mark.asyncio
async def test_local_command_collects_output_and_exit_status(transports):
    result = await run_local_command(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        timeout=5,
    )
    assert result == (3, b"out\n" if os.name == "posix" else b"out\r\n", b"err\n" if os.name == "posix" else b"err\r\n")
    _assert_closed(transports)


@pytest.mark.asyncio
async def test_cancel_during_spawn_drains_handle_before_propagating_first_message(monkeypatch, transports):
    loop = asyncio.get_running_loop()
    original = loop.subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_spawn(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(loop, "subprocess_exec", delayed_spawn)
    owner = asyncio.create_task(run_local_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=5))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for message in ("first", "second"):
            owner.cancel(message)
            await asyncio.sleep(0)
            assert not owner.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await owner
    assert caught.value.args == ("first",)
    _assert_closed(transports)


@pytest.mark.asyncio
async def test_local_command_timeout_reaps_process_and_closes_pipes(transports):
    with pytest.raises(TimeoutError):
        await run_local_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.05)
    _assert_closed(transports)


@pytest.mark.asyncio
async def test_local_command_spawn_failure_is_propagated(tmp_path):
    with pytest.raises(FileNotFoundError):
        await run_local_command([str(tmp_path / "missing-command")], timeout=1)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="owned POSIX process groups")
async def test_exited_shell_with_descendant_pipes_is_terminated_as_owned_group(tmp_path, transports):
    child_pid = tmp_path / "child.pid"
    child_source = (
        f"import os,time; from pathlib import Path; Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    source = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child_source!r}])"
    try:
        with pytest.raises(TimeoutError):
            await run_local_command([sys.executable, "-c", source], timeout=0.5)
        _assert_closed(transports)
        assert transports[0][0].get_returncode() == 0
        pid = int(child_pid.read_text())
        async with asyncio.timeout(5):
            while psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                await asyncio.sleep(0.01)
    finally:
        if child_pid.exists():
            try:
                psutil.Process(int(child_pid.read_text())).kill()
            except psutil.NoSuchProcess:
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("collector", ["linux", "mpstat", "windows_memory", "windows_cpu"])
async def test_health_collectors_propagate_cancel_after_owned_command_cleanup(monkeypatch, transports, collector):
    entered = asyncio.Event()
    original_communicate = asyncio.subprocess.Process.communicate

    async def communicate(self, *args, **kwargs):
        entered.set()
        return await original_communicate(self, *args, **kwargs)

    monkeypatch.setattr(asyncio.subprocess.Process, "communicate", communicate)

    async def command(_args, *, timeout):
        return await local_command.run_local_command(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=timeout
        )

    monkeypatch.setattr(health_checker, "run_local_command", command)
    monkeypatch.setattr(health_checker_metrics, "run_local_command", command)
    if collector == "linux":
        monkeypatch.setattr(health_checker.platform, "system", lambda: "Linux")
        operation = health_checker.HealthChecker._check_local_health(None)
    elif collector == "mpstat":
        operation = health_checker_metrics.collect_local_mpstat()
    else:
        method = getattr(
            health_checker_metrics,
            "windows_fill_memory_metrics" if collector == "windows_memory" else "windows_fill_cpu_metrics",
        )
        operation = method(InstanceMetrics(is_reachable=True))
    owner = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 5)
    owner.cancel("health stopped")
    with pytest.raises(asyncio.CancelledError, match="health stopped"):
        await owner
    _assert_closed(transports)
