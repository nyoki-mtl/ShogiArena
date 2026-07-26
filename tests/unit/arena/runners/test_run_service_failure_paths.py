"""run service の中断・失敗経路（task 0052 / decisions.md Decision 7・9）。

- cancellation と runtime error でも terminal status を残す。
  finalize へ到達しない経路なので、ここで書かないと公開文書の
  `status=failed / termination_reason=cancelled|runtime-error` が実運用で成立しない。
- prepare 中（dashboard 起動、session context 構築）の失敗でも service cleanup を走らせる。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.session.execution_service import (
    TournamentSessionExecutionService,
)
from shogiarena._core.contexts.game_session.application.session.flow import SessionFlow
from shogiarena._core.contexts.game_session.application.session.run_service import TournamentSessionRunService
from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.shared.kernel.session_hooks import SessionStopController


class _Progress:
    def __init__(self) -> None:
        self.finalized: list[dict[str, object]] = []

    def finalize(self, payload: dict[str, object]) -> None:
        self.finalized.append(payload)

    def on_game_complete(self, payload: dict[str, object]) -> None:
        del payload


class _RunnerStub:
    """run service が使う最小の runner。失敗させたい段階を注入する。"""

    def __init__(
        self,
        *,
        fail_at: str | None = None,
        fail_with: type[BaseException] = RuntimeError,
        cleanup_fails_with: BaseException | None = None,
    ) -> None:
        self._fail_at = fail_at
        self._fail_with = fail_with
        self._cleanup_fails_with = cleanup_fails_with
        self.session_phase = "starting"
        self.stop_calls = 0
        self.interrupted_reasons: list[RunTerminationReason] = []
        # (reason, is_provisional, cleanup_error) の呼び出し順（2 段 commit の確認用）。
        self.interrupted_writes: list[tuple[RunTerminationReason, bool, str | None]] = []
        # 各 write 時点の cleanup 回数。暫定 → cleanup → 確定 の順序を検証する。
        self.stop_calls_at_write: list[int] = []
        self._progress = _Progress()
        self._stop_controller = SessionStopController()
        self._services_closed = False

    def _maybe_fail(self, stage: str) -> None:
        if self._fail_at == stage:
            raise self._fail_with(f"injected failure at {stage}")

    # --- lifecycle -----------------------------------------------------
    async def prepare_run_dir(self) -> None:
        self._maybe_fail("prepare_run_dir")

    async def prepare_domain(self) -> None:
        self._maybe_fail("prepare_domain")

    async def init_services(self) -> None:
        self._maybe_fail("init_services")

    def get_dashboard_params(self) -> tuple[Any, str, int, int] | None:
        return (None, "127.0.0.1", 8000, 1)

    async def start_dashboard_server(self, run_dir: Any, host: str, preferred_port: int, num_workers: int) -> int:
        del run_dir, host, num_workers
        self._maybe_fail("start_dashboard_server")
        return preferred_port

    async def seed_initial_summary(self) -> None:
        self._maybe_fail("seed_initial_summary")

    def build_session_context(self) -> object | None:
        self._maybe_fail("build_session_context")
        return object()

    def set_session_context(self, session_context: object | None) -> None:
        del session_context

    async def stop_services(self) -> None:
        self.stop_calls += 1
        self._services_closed = True
        if self._cleanup_fails_with is not None:
            raise self._cleanup_fails_with

    def are_services_closed(self) -> bool:
        return self._services_closed

    def write_interrupted_run_health(
        self,
        reason: RunTerminationReason,
        *,
        is_provisional: bool = False,
        cleanup_error: str | None = None,
    ) -> None:
        self.interrupted_writes.append((reason, is_provisional, cleanup_error))
        self.stop_calls_at_write.append(self.stop_calls)
        if not is_provisional:
            self.interrupted_reasons.append(reason)

    # --- results -------------------------------------------------------
    async def calculate_results(self) -> object:
        return object()

    async def finalize_tournament(self, results: object) -> None:
        del results

    def get_sprt_status(self) -> object | None:
        return None

    @property
    def progress(self) -> _Progress:
        return self._progress

    def set_progress(self, reporter: _Progress) -> None:
        self._progress = reporter

    @property
    def stop_controller(self) -> SessionStopController:
        return self._stop_controller


class _RunLoopStub:
    def __init__(self, *, raise_with: BaseException | None = None) -> None:
        self._raise_with = raise_with
        self.calls = 0

    async def run_loop(self, runner: object, *, controller: object, session_context: object) -> None:
        del runner, controller, session_context
        self.calls += 1
        if self._raise_with is not None:
            raise self._raise_with


class _ResultBuilder:
    def build_tournament_run_result(self, results: object, sprt_status: object | None) -> object:
        del results, sprt_status
        return type("_Result", (), {"run_id": "r1"})()


async def _run(runner: _RunnerStub, run_loop: _RunLoopStub) -> object | None:
    return await TournamentSessionRunService().run(
        cast(Any, runner),
        execution_service=cast(Any, TournamentSessionExecutionService()),
        run_loop_service=cast(Any, run_loop),
        result_builder=cast(Any, _ResultBuilder()),
        progress_reporter=None,
    )


@pytest.mark.asyncio
async def test_cancellation_writes_a_terminal_status_and_stops_services() -> None:
    runner = _RunnerStub()
    run_loop = _RunLoopStub(raise_with=asyncio.CancelledError())

    assert await _run(runner, run_loop) is None

    assert runner.stop_calls >= 1
    assert runner.interrupted_reasons == [RunTerminationReason.CANCELLED]
    assert runner.stop_controller.reason == "cancelled"
    assert runner.progress.finalized == [{"status": "cancelled"}]


@pytest.mark.asyncio
async def test_keyboard_interrupt_is_treated_as_a_cancellation() -> None:
    runner = _RunnerStub()
    run_loop = _RunLoopStub(raise_with=KeyboardInterrupt())

    assert await _run(runner, run_loop) is None

    assert runner.interrupted_reasons == [RunTerminationReason.CANCELLED]


@pytest.mark.asyncio
async def test_runtime_error_writes_a_terminal_status_and_reraises() -> None:
    runner = _RunnerStub()
    run_loop = _RunLoopStub(raise_with=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await _run(runner, run_loop)

    assert runner.stop_calls >= 1
    assert runner.interrupted_reasons == [RunTerminationReason.RUNTIME_ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["start_dashboard_server", "seed_initial_summary", "build_session_context"],
)
async def test_prepare_failure_still_stops_services(stage: str) -> None:
    """``init_services`` 成功後に落ちても service を残さない（Decision 9）。"""

    runner = _RunnerStub(fail_at=stage)
    run_loop = _RunLoopStub()

    with pytest.raises(RuntimeError, match=f"injected failure at {stage}"):
        await _run(runner, run_loop)

    assert runner.stop_calls >= 1
    assert run_loop.calls == 0
    assert runner.interrupted_reasons == [RunTerminationReason.RUNTIME_ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raise_with", "reason"),
    [
        (asyncio.CancelledError(), RunTerminationReason.CANCELLED),
        (RuntimeError("boom"), RunTerminationReason.RUNTIME_ERROR),
    ],
    ids=["cancelled", "runtime-error"],
)
async def test_the_terminal_status_is_committed_in_two_phases(
    raise_with: BaseException,
    reason: RunTerminationReason,
) -> None:
    """暫定 → cleanup → 確定 の順で commit すること（Decision 7 の 2 段 commit）。

    cleanup 前に一度書くのは、cancel 済み task が次の ``await`` で再び
    ``CancelledError`` を投げるため。cleanup 後に昇格させないと、
    artifact が cleanup の結果を一切反映できない。
    """

    runner = _RunnerStub()
    run_loop = _RunLoopStub(raise_with=raise_with)

    try:
        await _run(runner, run_loop)
    except RuntimeError:
        pass

    assert runner.interrupted_writes == [(reason, True, None), (reason, False, None)]
    # 暫定は cleanup 前、確定は cleanup 後。
    assert runner.stop_calls_at_write == [0, 1]


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_the_primary_exception() -> None:
    """cleanup の失敗で元の異常終了理由を置き換えないこと。

    置き換えると、呼び出し側と artifact が実際の原因ではなく cleanup の失敗を指す。
    """

    runner = _RunnerStub(cleanup_fails_with=RuntimeError("cleanup failed"))
    run_loop = _RunLoopStub(raise_with=ValueError("primary failed"))

    with pytest.raises(ValueError, match="primary failed"):
        await _run(runner, run_loop)

    assert runner.interrupted_reasons == [RunTerminationReason.RUNTIME_ERROR]
    # cleanup の失敗は握り潰さず、確定 status に併記する。
    reason, is_provisional, cleanup_error = runner.interrupted_writes[-1]
    assert (reason, is_provisional) == (RunTerminationReason.RUNTIME_ERROR, False)
    assert cleanup_error == "RuntimeError: cleanup failed"


@pytest.mark.asyncio
async def test_cleanup_failure_during_cancellation_still_commits_the_status() -> None:
    runner = _RunnerStub(cleanup_fails_with=RuntimeError("cleanup failed"))
    run_loop = _RunLoopStub(raise_with=asyncio.CancelledError())

    assert await _run(runner, run_loop) is None

    assert runner.interrupted_reasons == [RunTerminationReason.CANCELLED]
    assert runner.interrupted_writes[-1][2] == "RuntimeError: cleanup failed"
    assert runner.progress.finalized == [{"status": "cancelled"}]


@pytest.mark.asyncio
async def test_clean_run_does_not_write_an_interrupted_status() -> None:
    runner = _RunnerStub()
    run_loop = _RunLoopStub()

    result = await _run(runner, run_loop)

    assert result is not None
    assert runner.interrupted_reasons == []


# --- SessionFlow（SPSA 経路） ------------------------------------------------


class _FlowRunnerStub(_RunnerStub):
    """``SessionFlow`` が要求する追加メソッドを持つ stub。"""

    def create_lifecycle_hooks(self, controller: SessionStopController) -> object:
        del controller
        return object()

    def set_lifecycle_hooks(self, hooks: object) -> None:
        del hooks

    async def create_orchestrator(self, hooks: object, session_context: object | None) -> object:
        del hooks, session_context
        raise AssertionError("must not be reached in these tests")

    async def run_pre_orchestration_hooks(self, orchestrator: object) -> None:
        del orchestrator

    async def run_orchestrator(self, orchestrator: object, run_coro: object) -> object | None:
        del orchestrator, run_coro
        return None

    async def finalize_and_persist(self, run_result: object | None) -> object | None:
        return run_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["start_dashboard_server", "seed_initial_summary", "build_session_context"],
)
async def test_session_flow_prepare_failure_still_stops_services(stage: str) -> None:
    runner = _FlowRunnerStub(fail_at=stage)
    flow: SessionFlow[object] = SessionFlow(cast(Any, runner))

    with pytest.raises(RuntimeError, match=f"injected failure at {stage}"):
        await flow.run()

    assert runner.stop_calls >= 1


# --- finalize 経路の cleanup failure（review 第3次 H2 続き） ------------------


class _FinalizeFailingRunner(_RunnerStub):
    """``finalize_tournament`` で落ち、その後の cleanup も落ちる runner。

    実装では finalize service が内部で cleanup を握って primary を再送出する。
    その後 ``finalize_session`` 側が **2 度目** の cleanup を呼ぶため、
    そこを無防備にしておくと primary がすり替わる。
    """

    def __init__(self) -> None:
        super().__init__()
        self.finalize_calls = 0

    async def finalize_tournament(self, results: object) -> None:
        del results
        self.finalize_calls += 1
        # finalize service 相当: 内部で cleanup を試みてから primary を送出する。
        self._services_closed = True
        raise ValueError("primary finalization failure")

    async def stop_services(self) -> None:
        self.stop_calls += 1
        self._services_closed = True
        raise RuntimeError("cleanup failure")


@pytest.mark.asyncio
async def test_cleanup_failure_after_finalize_failure_keeps_the_primary() -> None:
    """finalize 失敗後の cleanup 失敗で、元の finalization failure を置き換えないこと。"""

    runner = _FinalizeFailingRunner()
    run_loop = _RunLoopStub()

    with pytest.raises(ValueError, match="primary finalization failure"):
        await _run(runner, run_loop)

    assert runner.finalize_calls == 1
    assert runner.interrupted_reasons == [RunTerminationReason.RUNTIME_ERROR]


class _NoOpCleanupRunner(_RunnerStub):
    """finalize が service を閉じない runner（SPSA 経路の保険を確認する）。"""

    async def finalize_tournament(self, results: object) -> None:
        del results


@pytest.mark.asyncio
async def test_the_success_path_does_not_stop_services_twice() -> None:
    """finalize が既に閉じていれば、保険の cleanup を重ねて呼ばないこと。

    2 度呼ぶと、finalize 失敗時に 2 度目の失敗が primary を置き換える経路ができる。
    """

    class _ClosingRunner(_RunnerStub):
        async def finalize_tournament(self, results: object) -> None:
            del results
            # 実装の finalize service と同じく、ここで cleanup 済みにする。
            self.stop_calls += 1
            self._services_closed = True

    runner = _ClosingRunner()
    result = await _run(runner, _RunLoopStub())

    assert result is not None
    assert runner.stop_calls == 1


@pytest.mark.asyncio
async def test_the_success_path_still_stops_services_when_finalize_does_not() -> None:
    """finalize が閉じない実装では、保険の cleanup が働くこと（Decision 9）。"""

    runner = _NoOpCleanupRunner()
    result = await _run(runner, _RunLoopStub())

    assert result is not None
    assert runner.stop_calls == 1
