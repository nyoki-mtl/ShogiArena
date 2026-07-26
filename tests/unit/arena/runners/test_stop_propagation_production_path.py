"""停止要求が現在の controller まで届くことを production 経路で確認する。

task 0052 / review 第7次。第6次までの回帰は次の 2 点で production を通していなかった。

- reason 文字列を stub 側へ複製し、実際に停止を要求する API を呼んでいなかった。
- controller の差し替えを跨いでいなかったため、長寿命 producer（heartbeat）が
  起動時の controller を握ったままでも green になった。

ここでは次を **実物** で通す。

- `ScheduleMutationService.request_reschedule()`（reason 文字列はここが決める）
- `SessionStopController`（pause / terminal の判定とラッチ）
- `TournamentRunLoopService.run_loop()`（controller の差し替えを含む）
- `OpenBenchDelegate` の heartbeat（停止要求の producer）

stub にするのは外部境界だけ: schedule generator と OpenBench の HTTP client。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.adapters.openbench.delegate import OpenBenchDelegate
from shogiarena._core.contexts.game_session.application.session.run_loop_service import TournamentRunLoopService
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.schedule_context import TournamentScheduleContext
from shogiarena._core.contexts.tournament.application.session.schedule_mutation_service import ScheduleMutationService
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.shared.kernel.session_hooks import SessionStopController


def _spec(index: int) -> GameSpec:
    return GameSpec(
        game_id=f"g{index:04d}",
        black_engine="base",
        white_engine="tested",
        round_num=index,
        initial_sfen="startpos",
    )


class _SchedulerStub:
    """外部境界。日程生成そのものは本 test の対象ではない。"""

    def generate_schedule(self, **kwargs: Any) -> list[GameSpec]:
        del kwargs
        return [_spec(index) for index in range(10)]


def _config() -> Any:
    return SimpleNamespace(
        engines=[SimpleNamespace(name="base"), SimpleNamespace(name="tested")],
        tournament=SimpleNamespace(games_per_pair=10, seed="1", game_order="sequential"),
        rules=SimpleNamespace(initial_positions=SimpleNamespace(flip_policy="pair_both")),
    )


class _RunnerHarness:
    """run loop が触る面を production と同じ形で持つ harness。

    controller は **holder 経由** で保持する。production の
    ``SessionContextHolder.reset_stop_controller`` と同じく、差し替えると
    ``stop_controller`` プロパティが新しい instance を返す。
    """

    def __init__(self) -> None:
        self.session_phase = "starting"
        self.should_stop_when_idle = False
        self.dispatches = 0
        self.controller_resets = 0
        self.waits = 0
        self._controller = SessionStopController()
        self.state = TournamentRunnerState(game_schedule=[_spec(index) for index in range(10)])
        self._mutation = ScheduleMutationService()
        self._on_dispatch: list[Any] = []

    # --- controller ----------------------------------------------------
    @property
    def stop_controller(self) -> SessionStopController:
        return self._controller

    def reset_stop_controller(self, controller: SessionStopController) -> None:
        self._controller = controller
        self.controller_resets += 1

    def _schedule_ctx(self) -> TournamentScheduleContext:
        # production の facade と同じく、呼ぶたびに現在の controller で組み立てる。
        return TournamentScheduleContext(
            config=cast(Any, _config()),
            scheduler=cast(Any, _SchedulerStub()),
            instance_pool=None,
            run_dir=Path("."),
            stop_controller=self._controller,
            is_dashboard_enabled=False,
            engine_instance_defaults={},
            orchestrator=cast(Any, SimpleNamespace(request_stop=lambda: None)),
        )

    async def request_reschedule(self) -> Any:
        """production の dashboard API が呼ぶ経路をそのまま使う。"""
        return await self._mutation.request_reschedule(self.state, self._schedule_ctx())

    # --- run loop の面 --------------------------------------------------
    @property
    def pending_reschedule(self) -> Any:
        return self.state.pending_reschedule

    def has_pending_games(self) -> bool:
        return self.dispatches < 3

    def create_lifecycle_hooks(self, controller: Any) -> object:
        del controller
        return object()

    def set_lifecycle_hooks(self, hooks: object) -> None:
        del hooks

    async def create_orchestrator(self, hooks: object, session_context: object) -> object:
        del hooks, session_context
        return SimpleNamespace(run=lambda: None)

    async def run_pre_orchestration_hooks(self, orchestrator: object) -> None:
        del orchestrator

    async def run_orchestrator(self, orchestrator: object, run_coro: object) -> None:
        del orchestrator, run_coro
        self.dispatches += 1
        if self._on_dispatch:
            action = self._on_dispatch.pop(0)
            if action is not None:
                await action()

    @staticmethod
    def are_services_closed() -> bool:
        return False

    async def has_applied_pending_reschedule(self) -> bool:
        if self.state.pending_reschedule is None:
            return False
        self.state.game_schedule = self.state.pending_reschedule
        self.state.pending_reschedule = None
        return True

    async def wait_for_new_schedule(self) -> None:
        self.waits += 1
        self.dispatches = 99  # 待ったら終わらせる

    def schedule(self, *actions: Any) -> None:
        self._on_dispatch = list(actions)


async def _run_loop(harness: _RunnerHarness) -> None:
    await TournamentRunLoopService().run_loop(
        cast(Any, harness), controller=harness.stop_controller, session_context=None
    )


@pytest.mark.asyncio
async def test_the_production_reschedule_api_keeps_the_run_going() -> None:
    """実 API の reschedule で run が止まらないこと。

    reason 文字列は `ScheduleMutationService` が決める。test 側で複製しない。
    """

    harness = _RunnerHarness()
    harness.schedule(harness.request_reschedule)

    await _run_loop(harness)

    assert harness.dispatches > 1, "the reschedule must not end the run"
    assert harness.controller_resets >= 1


# ---------------------------------------------------------------------------
# controller 差し替えを跨いだ停止要求（review 第7次 H1）
# ---------------------------------------------------------------------------


class _HeartbeatClientStub:
    """外部境界（HTTP）。停止指示を返すことだけが役割。"""

    is_strict = True

    @staticmethod
    async def should_stop_after_heartbeat() -> bool:
        return True


def _delegate_with_heartbeat(provider: Any) -> tuple[OpenBenchDelegate, Any]:
    """実 delegate の heartbeat を、stub client で起動する。

    停止要求の伝播（provider 解決）は delegate の実コードを通る。
    """
    delegate = OpenBenchDelegate(config=cast(Any, _config()), run_dir=Path("."), should_skip_resume=True)
    client = _HeartbeatClientStub()
    delegate._client = cast(Any, client)
    delegate._start_heartbeat(
        client=cast(Any, client),
        interval_sec=0.01,
        stop_controller_provider=provider,
    )
    return delegate, client


@pytest.mark.asyncio
async def test_a_heartbeat_stop_reaches_the_controller_in_use_after_a_reschedule() -> None:
    """reschedule で controller が入れ替わった後でも、heartbeat の停止が現在の controller に届くこと。

    起動時の instance を握ると、停止は死んだ controller に立ち、実行中の run は
    次局を dispatch できてしまう（review 第7次 H1）。
    """

    harness = _RunnerHarness()
    delegate, _ = _delegate_with_heartbeat(lambda: harness.stop_controller)

    async def reschedule_then_heartbeat() -> None:
        await harness.request_reschedule()

    async def wait_for_heartbeat_stop() -> None:
        for _ in range(200):
            if harness.stop_controller.is_terminal:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the heartbeat stop never reached the controller in use")

    # 1局目で reschedule（controller が入れ替わる）、2局目で heartbeat の停止を待つ。
    harness.schedule(reschedule_then_heartbeat, wait_for_heartbeat_stop)

    try:
        await _run_loop(harness)
    finally:
        await delegate._stop_heartbeat()

    assert harness.controller_resets >= 1, "the reschedule must have swapped the controller"
    # 差し替え後の controller に停止が立ち、そこで run が終わる。
    assert harness.stop_controller.is_terminal
    assert harness.stop_controller.reason == "openbench-stop"
    assert harness.dispatches == 2, "no game may be dispatched after the terminal stop"
    assert harness.waits == 0


@pytest.mark.asyncio
async def test_a_heartbeat_stop_wakes_a_run_waiting_after_a_cancel() -> None:
    """WAIT 中にも heartbeat の terminal stop が loop を起床させること。

    provider で現在の controller に届いても、run loop が schedule event だけを待つと
    terminal stop を観測できず、OpenBench worker が終了しない。
    """

    class _WaitingHarness(_RunnerHarness):
        def __init__(self) -> None:
            super().__init__()
            self.schedule_available = asyncio.Event()

        async def wait_for_new_schedule(self) -> None:
            self.waits += 1
            await self.schedule_available.wait()

    harness = _WaitingHarness()
    delegate, _ = _delegate_with_heartbeat(lambda: harness.stop_controller)

    async def cancel_current_run() -> None:
        harness.stop_controller.request_stop(reason="cancelled")

    harness.schedule(cancel_current_run)

    try:
        await asyncio.wait_for(_run_loop(harness), timeout=2.0)
    finally:
        await delegate._stop_heartbeat()

    assert harness.waits == 1
    assert harness.controller_resets >= 1
    assert harness.stop_controller.is_terminal
    assert harness.stop_controller.reason == "openbench-stop"
    assert harness.dispatches == 1


@pytest.mark.asyncio
async def test_cancelling_a_waiting_run_reaps_both_child_waiters() -> None:
    """WAIT 中の parent cancellation で schedule/stop waiter を残さないこと。"""

    class _WaitingHarness(_RunnerHarness):
        def __init__(self) -> None:
            super().__init__()
            self.wait_started = asyncio.Event()
            self.wait_finished = asyncio.Event()
            self.schedule_available = asyncio.Event()

        async def wait_for_new_schedule(self) -> None:
            self.waits += 1
            self.wait_started.set()
            try:
                await self.schedule_available.wait()
            finally:
                self.wait_finished.set()

    harness = _WaitingHarness()

    async def cancel_current_run() -> None:
        harness.stop_controller.request_stop(reason="cancelled")

    harness.schedule(cancel_current_run)
    run_task = asyncio.create_task(_run_loop(harness))
    await asyncio.wait_for(harness.wait_started.wait(), timeout=1.0)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert harness.wait_finished.is_set()
    leaked_waiters = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and (
            "wait_for_new_schedule" in task.get_coro().__qualname__
            or "wait_until_stop_requested" in task.get_coro().__qualname__
        )
    ]
    assert leaked_waiters == []


@pytest.mark.asyncio
async def test_holding_the_controller_instance_would_miss_the_swap() -> None:
    """欠陥の再現: 起動時の instance を握ると、差し替え後の controller には届かない。

    修正前の delegate と同じ形（instance 束縛）を明示的に作り、
    現在の controller が停止しないままであることを固定する。
    """

    harness = _RunnerHarness()
    stale = harness.stop_controller
    delegate, _ = _delegate_with_heartbeat(lambda: stale)  # 起動時の instance に束縛

    await harness.request_reschedule()
    # reschedule を適用して controller を差し替える（run loop と同じ手順）。
    await harness.has_applied_pending_reschedule()
    harness.reset_stop_controller(SessionStopController())

    for _ in range(200):
        if stale.is_terminal:
            break
        await asyncio.sleep(0.01)
    await delegate._stop_heartbeat()

    assert stale.is_terminal, "the stale controller receives the stop"
    assert not harness.stop_controller.is_terminal, "the controller in use is left running"


# ---------------------------------------------------------------------------
# 制御状態と診断ラベルの一致（review 第7次 L3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pause_reason", ["cancelled", "reschedule"])
def test_a_reasonless_terminal_stop_drops_a_stale_pause_label(pause_reason: str) -> None:
    """理由の無い terminal 停止で、pause のラベルを残さないこと。

    残すと `is_terminal=True` なのに reason が pause という食い違いになる。
    """

    controller = SessionStopController()
    controller.request_stop(reason=pause_reason)
    assert not controller.is_terminal

    controller.request_stop()

    assert controller.is_terminal
    assert controller.reason is None


def test_a_reasonless_terminal_stop_keeps_a_terminal_label() -> None:
    """terminal のラベルは、理由の無い停止では落とさない（診断情報を捨てない）。"""

    controller = SessionStopController()
    controller.request_stop(reason="timeout-burst")

    controller.request_stop()

    assert controller.is_terminal
    assert controller.reason == "timeout-burst"


def test_a_pause_request_never_overwrites_a_terminal_label() -> None:
    controller = SessionStopController()
    controller.request_stop(reason="sprt-finished")
    controller.request_stop(reason="cancelled")

    assert controller.is_terminal
    assert controller.reason == "sprt-finished"


def test_a_pause_request_does_not_label_a_reasonless_terminal_stop() -> None:
    controller = SessionStopController()
    controller.request_stop()
    controller.request_stop(reason="cancelled")

    assert controller.is_terminal
    assert controller.reason is None
