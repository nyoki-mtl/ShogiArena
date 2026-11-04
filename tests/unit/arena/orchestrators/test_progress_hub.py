from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from shogiarena._core.contexts.game_session.application.progress.hub import ProgressHub


def _hub() -> ProgressHub:
    return ProgressHub(api_server=None, preassign_worker=lambda *_: None)


@pytest.mark.asyncio
async def test_cancelled_summary_updater_does_not_raise_or_reschedule() -> None:
    hub = _hub()
    started = asyncio.Event()
    calls = 0

    async def updater() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    errors: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        hub._schedule_summary_update(updater)
        await started.wait()
        hub._schedule_summary_update(updater)
        task = hub._summary_update_task
        assert task is not None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        assert errors == []
        assert calls == 1
        assert hub._summary_update_task is None
        assert not hub._is_summary_update_requested
    finally:
        loop.set_exception_handler(previous_handler)
        await hub.shutdown()


@pytest.mark.asyncio
async def test_summary_updater_coalesces_pending_requests() -> None:
    hub = _hub()
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    calls = 0

    async def updater() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        else:
            completed.set()

    hub._schedule_summary_update(updater)
    await started.wait()
    hub._schedule_summary_update(updater)
    hub._schedule_summary_update(updater)
    release.set()
    await asyncio.wait_for(completed.wait(), timeout=1)
    await hub.shutdown()

    assert calls == 2


@pytest.mark.asyncio
async def test_shutdown_waits_for_current_updater_without_rescheduling_pending_update() -> None:
    hub = _hub()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def updater() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    hub._schedule_summary_update(updater)
    await started.wait()
    hub._schedule_summary_update(updater)
    shutdown_task = asyncio.create_task(hub.shutdown())
    await asyncio.sleep(0)
    assert not shutdown_task.done()
    release.set()
    await shutdown_task
    hub._schedule_summary_update(updater)
    await asyncio.sleep(0)

    assert calls == 1
    assert hub._summary_update_task is None
    assert not hub._is_summary_update_requested
