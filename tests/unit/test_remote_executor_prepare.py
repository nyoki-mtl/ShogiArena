from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import cast

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePathResolver
from shogiarena._core.platform.engine_provisioning.remote_repo_preparer import RemoteRepoPreparer


class _PreparationFailure(Exception):
    pass


class _PathResolverStub:
    async def expand(self, remote_root: str) -> str:
        return f"/expanded/{remote_root}"


class _RepoPreparerStub:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.failure: BaseException | None = None
        self.repo_calls = 0

    async def ensure_repo(self, *, remote_root: str, absolute_root: str) -> None:
        del remote_root, absolute_root
        self.repo_calls += 1
        self.started.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure

    async def ensure_overlay(self, absolute_root: str) -> None:
        del absolute_root


class _RemoteExecutorHarness(RemoteExecutor):
    def __init__(self, key: str, preparer: _RepoPreparerStub) -> None:
        self._key = key
        self._path_resolver_stub = _PathResolverStub()
        self._repo_preparer_stub = preparer

    async def _ensure_connected(self) -> None:
        return None

    def _cache_key(self, remote_root: str) -> str:
        del remote_root
        return self._key

    @property
    def path_resolver(self) -> RemotePathResolver:
        return cast(RemotePathResolver, self._path_resolver_stub)

    @property
    def repo_preparer(self) -> RemoteRepoPreparer:
        return cast(RemoteRepoPreparer, self._repo_preparer_stub)


@pytest.fixture(autouse=True)
def _clear_remote_executor_caches() -> Iterator[None]:
    RemoteExecutor._prepared_roots.clear()  # noqa: SLF001
    RemoteExecutor._prepare_tasks.clear()  # noqa: SLF001
    yield
    RemoteExecutor._prepared_roots.clear()  # noqa: SLF001
    RemoteExecutor._prepare_tasks.clear()  # noqa: SLF001


@pytest.mark.asyncio
async def test_ensure_repo_propagates_owner_failure_to_concurrent_waiter() -> None:
    preparer = _RepoPreparerStub()
    preparer.failure = _PreparationFailure("prepare failed")
    executor = _RemoteExecutorHarness("failure", preparer)

    owner = asyncio.create_task(executor.ensure_repo("repo"))
    await preparer.started.wait()
    waiter = asyncio.create_task(executor.ensure_repo("repo"))
    await asyncio.sleep(0)
    preparer.release.set()

    for task in (owner, waiter):
        with pytest.raises(_PreparationFailure, match="prepare failed"):
            await asyncio.wait_for(task, timeout=0.2)

    assert preparer.repo_calls == 1
    assert RemoteExecutor._prepare_tasks == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_ensure_repo_cancels_waiter_when_owner_is_cancelled_and_allows_retry() -> None:
    preparer = _RepoPreparerStub()
    executor = _RemoteExecutorHarness("cancel", preparer)

    owner = asyncio.create_task(executor.ensure_repo("repo"))
    await preparer.started.wait()
    waiter = asyncio.create_task(executor.ensure_repo("repo"))
    await asyncio.sleep(0)
    owner.cancel()

    for task in (owner, waiter):
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)

    assert RemoteExecutor._prepare_tasks == {}  # noqa: SLF001

    preparer.release.set()
    await asyncio.wait_for(executor.ensure_repo("repo"), timeout=0.2)
    assert preparer.repo_calls == 2


@pytest.mark.asyncio
async def test_ensure_repo_cancelled_waiter_does_not_cancel_owner() -> None:
    preparer = _RepoPreparerStub()
    executor = _RemoteExecutorHarness("waiter-cancel", preparer)

    owner = asyncio.create_task(executor.ensure_repo("repo"))
    await preparer.started.wait()
    waiter = asyncio.create_task(executor.ensure_repo("repo"))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=0.2)

    preparer.release.set()
    await asyncio.wait_for(owner, timeout=0.2)
    assert preparer.repo_calls == 1
    assert "waiter-cancel" in RemoteExecutor._prepared_roots  # noqa: SLF001
    assert RemoteExecutor._prepare_tasks == {}  # noqa: SLF001
