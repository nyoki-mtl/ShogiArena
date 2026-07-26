"""停止要求による dispatch 打ち切りを異常扱いしない（task 0052）。

SPRT / OpenBench の run は「対局の失敗が検定を汚す」ため fail-fast する。
しかし **停止要求で開始しなかった局** は異常ではなく、正常な停止手順の一部である。

ここを一般の ``RuntimeError`` で表すと fail-fast が掴んでしまい、
SPRT の正常な早期終了が `status=failed` / `termination_reason=runtime-error` になる。
実 run でこの誤りを再現したうえで、専用型による分類を回帰として固定する。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.tournament.adapters.orchestrator import TournamentOrchestrator
from shogiarena._core.shared.kernel.dispatch_control import GameDispatchStoppedError


class _OrchestratorStub:
    """``run_pending_item`` が触る最小の面だけを持つ stub。"""

    def __init__(self, *, raise_with: BaseException | None, has_sprt: bool) -> None:
        self._raise_with = raise_with
        self.config = SimpleNamespace(sprt=object() if has_sprt else None, openbench=None)
        self.completions: list[str] = []
        self._schedule_metadata: dict[str, object] = {}

    async def _run_game(self, item: Any) -> None:
        del item
        if self._raise_with is not None:
            raise self._raise_with

    def _schedule_metadata_for(self, item: Any) -> None:
        del item
        return None

    @staticmethod
    def _build_error_game_record(item: Any, *, schedule_metadata: Any = None) -> object:
        del item, schedule_metadata
        return object()

    async def _emit_game_completion(self, *, game_id: str, game_info: Any, payload: Any, worker_idx: Any) -> None:
        del game_info, payload, worker_idx
        self.completions.append(game_id)


def _item(game_id: str = "g0001") -> Any:
    return SimpleNamespace(game_id=game_id)


async def _run(stub: _OrchestratorStub) -> None:
    await TournamentOrchestrator.run_pending_item(cast(Any, stub), _item())


@pytest.mark.asyncio
@pytest.mark.parametrize("has_sprt", [True, False])
async def test_dispatch_stop_is_not_treated_as_a_failure(has_sprt: bool) -> None:
    """停止要求で開始しなかった局は fail-fast させず、terminal record も作らない。"""

    stub = _OrchestratorStub(raise_with=GameDispatchStoppedError("stop requested"), has_sprt=has_sprt)

    await _run(stub)

    # 例外を伝播しない（伝播すると SPRT の正常終了が runtime error になる）。
    # 実行しなかった局なので completion も発行しない（`not_played` に残る）。
    assert stub.completions == []


@pytest.mark.asyncio
async def test_a_real_game_failure_still_fails_fast_for_sprt() -> None:
    """通常の対局失敗は従来どおり fail-fast する（統計を汚さないため）。"""

    stub = _OrchestratorStub(raise_with=RuntimeError("engine crashed"), has_sprt=True)

    with pytest.raises(RuntimeError, match="engine crashed"):
        await _run(stub)


@pytest.mark.asyncio
async def test_a_real_game_failure_is_isolated_for_plain_tournaments() -> None:
    """SPRT でない run は ERROR completion にして schedule を続ける（従来どおり）。"""

    stub = _OrchestratorStub(raise_with=RuntimeError("engine crashed"), has_sprt=False)

    await _run(stub)

    assert stub.completions == ["g0001"]


def test_dispatch_stop_error_is_not_a_plain_runtime_error_by_name_only() -> None:
    """``RuntimeError`` の部分型だが、専用型として分類できること。"""

    error = GameDispatchStoppedError("stop")
    assert isinstance(error, RuntimeError)
    assert type(error) is not RuntimeError


@pytest.mark.asyncio
async def test_resource_wait_raises_the_dispatch_stop_error_on_stop() -> None:
    """resource 待ち中に停止要求が立ったら専用型で抜けること。"""

    from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
        await_instance_resources,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    class _Pool:
        @staticmethod
        def try_acquire_resources(requirements: Any) -> bool:
            del requirements
            return False

    owner = SimpleNamespace(
        _stop_event=stop_event,
        _resource_poll_interval=0.01,
        _resource_poll_max_interval=0.01,
    )

    with pytest.raises(GameDispatchStoppedError):
        await await_instance_resources(
            cast(Any, owner),
            cast(Any, _Pool()),
            {},
            game_id="g0001",
        )


@pytest.mark.asyncio
async def test_stop_wins_over_an_available_resource() -> None:
    """停止済みなら、resource が空いていても取得せずに打ち切ること（review M4）。

    acquire を先に試す実装では、停止直後にちょうど resource が空いた局だけが開始される。
    SPRT ではその局が decision 確定後の後着標本になり、検定結果を動かしうる。
    """

    from shogiarena._core.contexts.game_session.adapters.orchestration.resource_control import (
        await_instance_resources,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    class _AlwaysAvailablePool:
        def __init__(self) -> None:
            self.acquire_calls = 0

        def try_acquire_resources(self, requirements: Any) -> bool:
            del requirements
            self.acquire_calls += 1
            return True

    pool = _AlwaysAvailablePool()
    owner = SimpleNamespace(
        _stop_event=stop_event,
        _resource_poll_interval=0.01,
        _resource_poll_max_interval=0.01,
    )

    with pytest.raises(GameDispatchStoppedError):
        await await_instance_resources(cast(Any, owner), cast(Any, pool), {}, game_id="g0001")

    # 予約してから打ち切ると slot が解放されずに残る。取得自体を試みないこと。
    assert pool.acquire_calls == 0
