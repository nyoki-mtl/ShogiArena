"""resume で復元した停止判断を dispatch 前に効かせる（task 0052 / review 第4次 H1）。

停止判断は run 中に「局の完了」を契機として下される。resume 直後にはその契機が無いため、
再評価しないと **既に停止すべき run が再び走り出す**。

- SPRT: 決着済みの run が数局進む。
- breaker: 次の正常局が先に完了すると連続数が消え、安全停止そのものを回避できる。

後者は「無駄が出る」では済まない。閾値の直前で中断して resume する操作を繰り返せば、
breaker を回避したまま走り続けられる。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.sprt_service import Sprt
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.session_hooks import SessionStopController
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin
from shogiarena._core.shared.kernel.timeout_breaker import TIMEOUT_BREAKER_POLICIES

_UNKNOWN = TimeoutOrigin.UNKNOWN.value


def _decided_sprt(*, latched: bool) -> Sprt:
    """実 run と同じ標本（30W/11D/4L）で accept_h1 に到達した SPRT。"""
    sprt = Sprt(elo0=0.0, elo1=20.0, alpha=0.05, beta=0.05)
    for _ in range(4):
        sprt.add_game_result(GameResult.BLACK_WIN)
    for _ in range(11):
        sprt.add_game_result(GameResult.DRAW_BY_REPETITION)
    for _ in range(30):
        sprt.add_game_result(GameResult.WHITE_WIN)
    assert sprt.is_finished()
    if latched:
        sprt.latch_decision()
    return sprt


def _runner(
    *,
    sprt: Sprt | None = None,
    min_games: int = 10,
    totals: dict[str, int] | None = None,
    consecutive: dict[str, int] | None = None,
    completed: int = 45,
) -> tuple[Any, SessionStopController]:
    controller = SessionStopController()
    state = SimpleNamespace(
        sprt=sprt,
        sprt_pair=("tested", "base") if sprt is not None else None,
        sprt_min_games=min_games,
        invalid_timeouts_by_origin=totals or {},
        consecutive_invalid_timeouts_by_origin=consecutive or {},
        completed_game_ids={f"g{index:04d}" for index in range(completed)},
    )
    runner = SimpleNamespace(_state=state, stop_controller=controller)
    return runner, controller


def _reapply(runner: Any) -> None:
    TournamentRunner._reapply_restored_stop_decisions(cast(Any, runner))


def test_a_latched_sprt_run_stops_before_dispatch() -> None:
    """ラッチ済みで resume したら、1局も投入せずに停止すること。"""

    runner, controller = _runner(sprt=_decided_sprt(latched=True))

    _reapply(runner)

    assert not controller.should_continue()
    assert controller.reason == "sprt-finished"


def test_a_decided_but_unlatched_sprt_run_stops_and_latches() -> None:
    """state.json がラッチ前に保存されていても、決着済みなら停止してラッチすること。"""

    sprt = _decided_sprt(latched=False)
    runner, controller = _runner(sprt=sprt)

    _reapply(runner)

    assert controller.reason == "sprt-finished"
    assert sprt.is_decision_latched


def test_a_decided_sprt_below_min_games_keeps_running() -> None:
    """`min_games` に届いていなければ止めないこと（早すぎる停止を作らない）。"""

    sprt = _decided_sprt(latched=False)
    runner, controller = _runner(sprt=sprt, min_games=10_000)

    _reapply(runner)

    assert controller.should_continue()
    assert controller.reason is None
    assert not sprt.is_decision_latched


def test_an_undecided_sprt_run_keeps_running() -> None:
    sprt = Sprt(elo0=0.0, elo1=20.0, alpha=0.05, beta=0.05)
    sprt.add_game_result(GameResult.WHITE_WIN)
    runner, controller = _runner(sprt=sprt)

    _reapply(runner)

    assert controller.should_continue()


@pytest.mark.parametrize("origin", list(TIMEOUT_BREAKER_POLICIES))
def test_a_tripped_breaker_stops_before_dispatch(origin: str) -> None:
    """閾値到達済みの counter で resume したら、1局も投入せずに停止すること。

    止めずに続けると、次の正常局が先に完了した時点で連続数が消え、
    breaker 停止そのものを回避できてしまう。
    """

    policy = TIMEOUT_BREAKER_POLICIES[origin]
    limit = policy.consecutive_limit
    runner, controller = _runner(
        totals={origin: limit},
        consecutive={origin: limit},
        completed=limit,
    )

    _reapply(runner)

    assert not controller.should_continue()
    assert controller.reason == policy.termination_reason


def test_a_breaker_below_the_threshold_keeps_running() -> None:
    policy = TIMEOUT_BREAKER_POLICIES[_UNKNOWN]
    runner, controller = _runner(
        totals={_UNKNOWN: policy.consecutive_limit - 1},
        consecutive={_UNKNOWN: policy.consecutive_limit - 1},
        # 比率でも止まらない件数にする。
        completed=1000,
    )

    _reapply(runner)

    assert controller.should_continue()
    assert controller.reason is None


def test_a_run_without_sprt_or_breaker_state_keeps_running() -> None:
    runner, controller = _runner(completed=0)

    _reapply(runner)

    assert controller.should_continue()


# ---------------------------------------------------------------------------
# run loop 側: 停止済みで反復へ入ったら 1 局も投入しない
# ---------------------------------------------------------------------------


class _LoopRunnerStub:
    """``run_iteration`` が触る最小の面。"""

    def __init__(self) -> None:
        self.session_phase = "starting"
        self.orchestrator_calls = 0

    @staticmethod
    def has_pending_games() -> bool:
        return True

    def create_lifecycle_hooks(self, controller: Any) -> object:
        del controller
        self.orchestrator_calls += 1
        return object()

    def set_lifecycle_hooks(self, hooks: object) -> None:
        del hooks

    async def create_orchestrator(self, hooks: object, session_context: object) -> object:
        del hooks, session_context
        self.orchestrator_calls += 1
        return SimpleNamespace(run=lambda: None)

    async def run_pre_orchestration_hooks(self, orchestrator: object) -> None:
        del orchestrator

    async def run_orchestrator(self, orchestrator: object, run_coro: object) -> None:
        del orchestrator, run_coro


@pytest.mark.asyncio
async def test_the_run_loop_skips_dispatch_when_stop_is_already_requested() -> None:
    """反復に入る前から停止済みなら orchestrator を作らないこと。

    loop は「まず 1 反復走らせてから停止を判定する」構造なので、ここで返さないと
    resume で復元した停止判断が空振りする。
    """

    from shogiarena._core.contexts.game_session.application.session.run_loop_service import (
        TournamentRunLoopService,
    )

    runner = _LoopRunnerStub()
    controller = SessionStopController()
    controller.request_stop(reason="sprt-finished")

    await TournamentRunLoopService().run_iteration(cast(Any, runner), controller, session_context=None)

    assert runner.orchestrator_calls == 0
    assert runner.session_phase == "stopping"


@pytest.mark.asyncio
async def test_the_run_loop_dispatches_normally_without_a_stop_request() -> None:
    from shogiarena._core.contexts.game_session.application.session.run_loop_service import (
        TournamentRunLoopService,
    )

    runner = _LoopRunnerStub()

    await TournamentRunLoopService().run_iteration(cast(Any, runner), SessionStopController(), session_context=None)

    assert runner.orchestrator_calls > 0
    assert runner.session_phase == "draining"


# ---------------------------------------------------------------------------
# pending reschedule と safety stop の優先順位（review 第5次 H1）
# ---------------------------------------------------------------------------


class _DecideRunnerStub:
    def __init__(self, *, pending_reschedule: bool) -> None:
        self.session_phase = "draining"
        self.pending_reschedule = pending_reschedule
        self.should_stop_when_idle = False
        self.applied_reschedule = False

    @staticmethod
    def are_services_closed() -> bool:
        return False

    async def has_applied_pending_reschedule(self) -> bool:
        self.applied_reschedule = True
        return self.pending_reschedule

    @staticmethod
    def has_pending_games() -> bool:
        return True


async def _decide(runner: Any, controller: SessionStopController) -> Any:
    from shogiarena._core.contexts.game_session.application.session.run_loop_service import (
        TournamentRunLoopService,
    )

    return await TournamentRunLoopService().decide_next(cast(Any, runner), controller)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["timeout-attribution-unknown", "timeout-burst", "sprt-finished", "openbench-stop", "openbench-heartbeat-error"],
)
async def test_a_pending_reschedule_does_not_override_a_run_ending_stop(reason: str) -> None:
    """run を終わらせる停止は pending reschedule より優先すること。

    先に reschedule を適用すると controller が作り直され、停止理由ごと消えて
    次の反復で dispatch できてしまう。
    """

    from shogiarena._core.contexts.game_session.application.session.reschedule_loop import RescheduleAction

    runner = _DecideRunnerStub(pending_reschedule=True)
    controller = SessionStopController()
    controller.request_stop(reason=reason)

    decision = await _decide(runner, controller)

    assert decision.action is RescheduleAction.STOP
    assert not decision.should_reset_controller
    # reschedule を適用しない（controller を作り直させない）。
    assert not runner.applied_reschedule


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["cancelled", "reschedule"])
async def test_a_pending_reschedule_still_applies_during_a_pause(reason: str) -> None:
    """pause では従来どおり reschedule を適用して継続すること。

    production の通常 reschedule は ``reason="reschedule"`` で停止を要求する。
    ここを terminal と誤判定すると、実行中の reschedule が必ず破棄される。
    """

    from shogiarena._core.contexts.game_session.application.session.reschedule_loop import RescheduleAction

    runner = _DecideRunnerStub(pending_reschedule=True)
    controller = SessionStopController()
    controller.request_stop(reason=reason)

    decision = await _decide(runner, controller)

    assert decision.action is RescheduleAction.CONTINUE
    assert decision.should_reset_controller
    assert runner.applied_reschedule


@pytest.mark.asyncio
async def test_a_cancel_does_not_release_a_terminal_stop() -> None:
    """確定した停止を cancel で解除できないこと（review 第6次 H2）。

    reason は診断ラベルであり、制御は ``is_terminal`` で決める。ラベルで制御すると、
    SPRT 決着後の cancel が WAIT → controller reset → 再 dispatch を許してしまう。
    """

    from shogiarena._core.contexts.game_session.application.session.reschedule_loop import RescheduleAction

    for order in (("sprt-finished", "cancelled"), ("cancelled", "sprt-finished")):
        runner = _DecideRunnerStub(pending_reschedule=True)
        controller = SessionStopController()
        for reason in order:
            controller.request_stop(reason=reason)

        decision = await _decide(runner, controller)

        assert controller.is_terminal, order
        assert decision.action is RescheduleAction.STOP, order
        assert not decision.should_reset_controller, order
        assert controller.reason == "sprt-finished", order


@pytest.mark.asyncio
async def test_a_user_pause_without_a_reschedule_waits() -> None:
    from shogiarena._core.contexts.game_session.application.session.reschedule_loop import RescheduleAction

    runner = _DecideRunnerStub(pending_reschedule=False)
    controller = SessionStopController()
    controller.request_stop(reason="cancelled")

    decision = await _decide(runner, controller)

    assert decision.action is RescheduleAction.WAIT
    assert decision.should_reset_controller


# ---------------------------------------------------------------------------
# 停止理由の優先順位（review 第5次 M3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "order",
    [("timeout-attribution-unknown", "sprt-finished"), ("sprt-finished", "timeout-attribution-unknown")],
    ids=["breaker-first", "sprt-first"],
)
def test_the_stop_reason_does_not_depend_on_the_request_order(order: tuple[str, str]) -> None:
    """同じ状態なら、要求順が違っても同じ理由になること。

    live は breaker → SPRT、resume は SPRT → breaker の順に評価する。後勝ちにすると、
    resume したかどうかだけで `clean` と `failed` が入れ替わる。
    """

    controller = SessionStopController()
    for reason in order:
        controller.request_stop(reason=reason)

    assert controller.reason == "timeout-attribution-unknown"


def test_an_sprt_decision_does_not_downgrade_a_safety_stop() -> None:
    controller = SessionStopController()
    controller.request_stop(reason="transport-timeout")
    controller.request_stop(reason="sprt-finished")

    assert controller.reason == "transport-timeout"


def test_the_first_reason_wins_at_equal_precedence() -> None:
    controller = SessionStopController()
    controller.request_stop(reason="timeout-burst")
    controller.request_stop(reason="transport-timeout")

    assert controller.reason == "timeout-burst"


def test_a_known_terminal_reason_replaces_an_unrecognized_one() -> None:
    controller = SessionStopController()
    controller.request_stop(reason="something-nobody-registered")
    controller.request_stop(reason="timeout-burst")

    assert controller.reason == "timeout-burst"


def test_an_unrecognized_reason_is_terminal_and_outranks_a_pause() -> None:
    """判別できない停止で run を待たせ続けない（安全側へ倒す）。"""

    controller = SessionStopController()
    controller.request_stop(reason="something-nobody-registered")

    assert controller.is_terminal
    controller.request_stop(reason="cancelled")
    assert controller.is_terminal
    assert controller.reason == "something-nobody-registered"


def test_a_stop_without_a_reason_is_terminal() -> None:
    controller = SessionStopController()
    controller.request_stop()

    assert controller.is_terminal
    assert controller.reason is None


# ---------------------------------------------------------------------------
# run_loop を通した挙動（decide_next 単体では見えない controller reset を含む）
# ---------------------------------------------------------------------------


class _FullLoopRunnerStub:
    """``run_loop`` が触る面をすべて持つ stub。

    ``reschedule_at`` 回目の反復の後に、production と同じ reason で reschedule 停止を要求する。
    """

    def __init__(self, *, reschedule_at: int | None, terminal_reason: str | None = None) -> None:
        self.session_phase = "starting"
        self.pending_reschedule = False
        self.should_stop_when_idle = False
        self.dispatches = 0
        self.waits = 0
        self.controller_resets = 0
        self._controller = SessionStopController()
        self._reschedule_at = reschedule_at
        self._terminal_reason = terminal_reason
        self._iterations = 0
        self._max_iterations = 4

    # --- controller ----------------------------------------------------
    def reset_stop_controller(self, controller: SessionStopController) -> None:
        self._controller = controller
        self.controller_resets += 1

    # --- iteration -----------------------------------------------------
    def has_pending_games(self) -> bool:
        return self._iterations < self._max_iterations

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
        self._iterations += 1
        if self._terminal_reason is not None and self._iterations == 1:
            self._controller.request_stop(reason=self._terminal_reason)
        if self._reschedule_at is not None and self._iterations == self._reschedule_at:
            # production の schedule mutation と同じ経路。
            self.pending_reschedule = True
            self._controller.request_stop(reason="reschedule")

    # --- decide --------------------------------------------------------
    @staticmethod
    def are_services_closed() -> bool:
        return False

    async def has_applied_pending_reschedule(self) -> bool:
        if not self.pending_reschedule:
            return False
        self.pending_reschedule = False
        return True

    async def wait_for_new_schedule(self) -> None:
        self.waits += 1
        self._max_iterations = self._iterations  # 待ったら終わらせる


async def _run_full_loop(runner: _FullLoopRunnerStub) -> None:
    from shogiarena._core.contexts.game_session.application.session.run_loop_service import (
        TournamentRunLoopService,
    )

    await TournamentRunLoopService().run_loop(cast(Any, runner), controller=runner._controller, session_context=None)


@pytest.mark.asyncio
async def test_a_normal_reschedule_keeps_the_run_going() -> None:
    """通常の reschedule で run が止まらないこと（review 第6次 H1 の回帰）。"""

    runner = _FullLoopRunnerStub(reschedule_at=1)

    await _run_full_loop(runner)

    # reschedule を適用して継続するので、2 回目以降も dispatch される。
    assert runner.dispatches > 1
    assert runner.controller_resets >= 1


@pytest.mark.asyncio
async def test_a_terminal_stop_ends_the_loop_even_with_a_pending_reschedule() -> None:
    """確定停止と reschedule が同時に立っても、run を再開させないこと。"""

    runner = _FullLoopRunnerStub(reschedule_at=1, terminal_reason="timeout-attribution-unknown")

    await _run_full_loop(runner)

    assert runner.dispatches == 1
    assert runner.waits == 0
    assert runner.controller_resets == 0
