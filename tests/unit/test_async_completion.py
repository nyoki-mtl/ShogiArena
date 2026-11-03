"""Owned operations drain without discarding caller cancellation context."""

from __future__ import annotations

import asyncio

import pytest

from shogiarena._core.shared.kernel.async_completion import complete_before_cancelling


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_first_message_and_count() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation() -> None:
        entered.set()
        await release.wait()

    owner = asyncio.create_task(complete_before_cancelling(operation()))
    await entered.wait()
    for message in ("original stop request", "second stop request"):
        owner.cancel(message)
        await asyncio.sleep(0)
        assert not owner.done()
    release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await owner
    assert caught.value.args == ("original stop request",)
    assert owner.cancelling() == 2


@pytest.mark.asyncio
async def test_timeout_translates_its_own_cancellation_after_operation_drains() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    scope = asyncio.timeout(None)
    finished = False

    async def operation() -> None:
        nonlocal finished
        entered.set()
        await release.wait()
        finished = True

    async def worker() -> None:
        async with scope:
            await complete_before_cancelling(operation())

    owner = asyncio.create_task(worker())
    await entered.wait()
    scope.reschedule(asyncio.get_running_loop().time() - 1)
    while not owner.cancelling():
        await asyncio.sleep(0)
    assert not owner.done() and not finished
    release.set()
    with pytest.raises(TimeoutError):
        await owner
    assert finished and owner.cancelling() == 0


@pytest.mark.asyncio
async def test_task_group_sibling_failure_waits_for_owned_operation() -> None:
    entered, release, fail = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = False
    owners = []

    async def operation() -> None:
        nonlocal finished
        entered.set()
        await release.wait()
        finished = True

    async def sibling() -> None:
        await fail.wait()
        raise ValueError("sibling failed")

    async def group() -> None:
        async with asyncio.TaskGroup() as tasks:
            owners.append(tasks.create_task(complete_before_cancelling(operation())))
            tasks.create_task(sibling())

    task = asyncio.create_task(group())
    await entered.wait()
    fail.set()
    while not owners[0].cancelling():
        await asyncio.sleep(0)
    assert not task.done() and not finished
    release.set()
    with pytest.raises(ExceptionGroup) as caught:
        await task
    assert [str(exc) for exc in caught.value.exceptions] == ["sibling failed"]
    assert finished and owners[0].cancelled()


@pytest.mark.asyncio
async def test_operation_failure_takes_precedence_over_caller_cancellation() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    failure = OSError("disk failed")

    async def operation() -> None:
        entered.set()
        await release.wait()
        raise failure

    owner = asyncio.create_task(complete_before_cancelling(operation()))
    await entered.wait()
    owner.cancel("stop")
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(OSError) as caught:
        await owner
    assert caught.value is failure


@pytest.mark.asyncio
async def test_operation_cancellation_is_propagated() -> None:
    operation = asyncio.create_task(asyncio.Event().wait())
    operation.cancel("operation cancelled")
    with pytest.raises(asyncio.CancelledError):
        await complete_before_cancelling(operation)
