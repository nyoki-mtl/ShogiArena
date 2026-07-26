"""Finalize の terminal commit 順序と failure injection（task 0052 / review finding H2）。

``completion_status.json`` と ``completed.flag`` は、失敗しうる finalize 処理と
service cleanup がすべて終わった後にだけ書く。BTD 推定や cleanup が失敗した run に
clean な artifact と marker を残さないことを回帰として固定する。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.application.summary.finalize_service import (
    TournamentSummaryFinalizeService,
)
from shogiarena._core.contexts.game_session.application.summary.runtime_context import (
    SummaryRuntimeActionRefs,
    SummaryRuntimeBuildRequest,
    SummaryRuntimeDependencies,
    SummaryRuntimeStateRefs,
    TournamentSummaryRuntimeContext,
)
from shogiarena._core.contexts.game_session.domain.run_health import RunTerminationReason
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.session_hooks import SessionStopController


class _PayloadStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_json_object(self) -> dict[str, object]:
        return dict(self._payload)


class _FinalPayloadServiceStub:
    def build_results_payload(self, results: TournamentResults) -> _PayloadStub:
        return _PayloadStub({"completed": results.completed_games_count})

    def build_final_btd_payload(self, *, results: TournamentResults, btd: object) -> _PayloadStub:
        del btd
        return _PayloadStub({"total": results.total_games})


class _ArtifactServiceStub:
    def __init__(self, *, fail_summary_write: bool = False) -> None:
        self.fail_summary_write = fail_summary_write
        self.summary_writes = 0

    def write_tournament_results(self, run_dir: Path, payload: dict[str, object]) -> None:
        del run_dir, payload

    def write_summary_btd(self, run_dir: Path, payload: dict[str, object], *, log_context: str) -> None:
        del run_dir, payload, log_context
        self.summary_writes += 1
        if self.fail_summary_write:
            raise OSError("injected summary_btd write failure")


class _ReportingServiceStub:
    def __init__(self, *, fail_btd: bool = False) -> None:
        self.fail_btd = fail_btd

    def log_tournament_results(self, results: TournamentResults) -> None:
        del results

    def estimate_btd(self, *, games: list[object], anchor_name: str | None, engine_names: list[str]) -> object:
        del games, anchor_name, engine_names
        if self.fail_btd:
            raise RuntimeError("injected BTD estimation failure")
        return SimpleNamespace(anchor="engine-a")

    def log_btd_estimates(self, *, results: TournamentResults, btd: object, engine_names: list[str]) -> None:
        del results, btd, engine_names


class _DbServiceStub:
    def get_games_with_players(self, *, game_type: str) -> list[object]:
        del game_type
        return []


async def _noop_async() -> None:
    return None


def _results() -> TournamentResults:
    return TournamentResults(
        engine_stats={},
        pair_results={},
        completed_games=["g1", "g2"],
        total_games=2,
        completed_games_count=2,
        cancelled_games_count=0,
    )


def _build_runtime(
    run_dir: Path,
    *,
    stop_calls: list[str],
    stop_controller: SessionStopController | None = None,
    fail_stop_services: bool = False,
    flush_openbench_raises: BaseException | None = None,
) -> TournamentSummaryRuntimeContext:
    async def stop_services() -> None:
        stop_calls.append("stop_services")
        if fail_stop_services:
            raise RuntimeError("injected service cleanup failure")

    async def flush_openbench() -> None:
        if flush_openbench_raises is not None:
            raise flush_openbench_raises

    return TournamentSummaryRuntimeContext(
        request=SummaryRuntimeBuildRequest(
            run_dir=run_dir,
            config=SimpleNamespace(engines=[SimpleNamespace(name="engine-a")]),
            engine_metadata=[],
            engine_time_controls=({}, None),
            summary_source="tournament",
            is_dashboard_enabled=False,
        ),
        state=SummaryRuntimeStateRefs(
            cancelled_game_ids=set(),
            game_schedule=[],
            completed_game_ids={"g1", "g2"},
            original_total_games=2,
        ),
        dependencies=SummaryRuntimeDependencies(
            db_service=_DbServiceStub(),
            api_server=None,
            record_writer=None,
            sprt_service=None,
            stop_controller=stop_controller,
        ),
        actions=SummaryRuntimeActionRefs(
            engine_instance_defaults=dict,
            resolve_tournament_type=lambda: "round_robin",
            build_rules_payload=dict,
            build_sprt_payload=dict,
            is_generate_run=lambda: False,
            get_schedule_snapshot=_noop_async,
            flush_openbench=flush_openbench,
            save_run_state=lambda _is_finished: None,
            update_dashboard=_noop_async,
            stop_services=stop_services,
        ),
    )


def _service(
    *,
    fail_btd: bool = False,
    fail_summary_write: bool = False,
) -> TournamentSummaryFinalizeService:
    return TournamentSummaryFinalizeService(
        payload_service=_FinalPayloadServiceStub(),
        artifact_service=_ArtifactServiceStub(fail_summary_write=fail_summary_write),
        reporting_service=_ReportingServiceStub(fail_btd=fail_btd),
    )


@pytest.mark.asyncio
async def test_clean_run_commits_status_then_flag(tmp_path: Path) -> None:
    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls)

    await _service().finalize(runtime, results=_results())

    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "clean"
    assert status["termination_reason"] == "schedule-complete"
    assert (tmp_path / "completed.flag").exists()
    assert stop_calls == ["stop_services"]


@pytest.mark.asyncio
async def test_btd_failure_leaves_no_clean_artifact_and_stops_services(tmp_path: Path) -> None:
    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls)

    with pytest.raises(RuntimeError, match="injected BTD estimation failure"):
        await _service(fail_btd=True).finalize(runtime, results=_results())

    assert not (tmp_path / "completed.flag").exists()
    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "finalization-error"
    # cleanup は例外経路でも走る（service leak を残さない）。
    assert stop_calls == ["stop_services"]


@pytest.mark.asyncio
async def test_final_summary_write_failure_leaves_no_clean_artifact(tmp_path: Path) -> None:
    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls)

    with pytest.raises(OSError, match="injected summary_btd write failure"):
        await _service(fail_summary_write=True).finalize(runtime, results=_results())

    assert not (tmp_path / "completed.flag").exists()
    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert stop_calls == ["stop_services"]


@pytest.mark.asyncio
async def test_cleanup_failure_leaves_no_clean_artifact(tmp_path: Path) -> None:
    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls, fail_stop_services=True)

    with pytest.raises(RuntimeError, match="injected service cleanup failure"):
        await _service().finalize(runtime, results=_results())

    assert not (tmp_path / "completed.flag").exists()
    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "cleanup-error"


@pytest.mark.asyncio
async def test_failure_removes_a_stale_completed_flag(tmp_path: Path) -> None:
    """前回実行の marker が残っていても、失敗した run を完了済みに見せない。"""

    (tmp_path / "completed.flag").touch()
    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls)

    with pytest.raises(RuntimeError):
        await _service(fail_btd=True).finalize(runtime, results=_results())

    assert not (tmp_path / "completed.flag").exists()


@pytest.mark.asyncio
async def test_stop_reason_is_carried_into_the_terminal_status(tmp_path: Path) -> None:
    controller = SessionStopController()
    controller.request_stop(reason="cancelled")
    runtime = _build_runtime(tmp_path, stop_calls=[], stop_controller=controller)

    await _service().finalize(runtime, results=_results())

    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupt",
    [asyncio.CancelledError(), KeyboardInterrupt()],
    ids=["cancelled", "keyboard-interrupt"],
)
async def test_cancellation_during_finalize_is_not_a_finalization_error(
    tmp_path: Path,
    interrupt: BaseException,
) -> None:
    """finalize 中の cancellation を `finalization-error` に固定しないこと（review M4）。

    ここで確定 status を書いてしまうと、中断経路が書こうとする `cancelled` が
    既存 status 保護に阻まれ、利用者の停止が故障として記録される。
    cleanup だけ行い、terminal status は中断経路（2 段 commit）へ委ねる。
    """

    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls, flush_openbench_raises=interrupt)

    with pytest.raises(type(interrupt)):
        await _service().finalize(runtime, results=_results())

    # cleanup は走る（service を残さない）。
    assert stop_calls == ["stop_services"]
    # terminal status はここでは書かない。中断経路が `cancelled` を書けるようにする。
    assert not (tmp_path / "completion_status.json").exists()
    assert not (tmp_path / "completed.flag").exists()


@pytest.mark.asyncio
async def test_a_cancelled_run_can_still_commit_its_terminal_status_afterwards(tmp_path: Path) -> None:
    """finalize が cancellation で抜けた後、中断経路が `cancelled` を書けること。"""

    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls, flush_openbench_raises=asyncio.CancelledError())
    service = _service()

    with pytest.raises(asyncio.CancelledError):
        await service.finalize(runtime, results=_results())

    # 中断経路の 2 段 commit を再現する。
    service.write_interrupted_status(tmp_path, RunTerminationReason.CANCELLED, results=_results(), is_provisional=True)
    service.write_interrupted_status(tmp_path, RunTerminationReason.CANCELLED, results=_results())

    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["termination_reason"] == "cancelled"
    assert status["is_provisional"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupt",
    [asyncio.CancelledError(), KeyboardInterrupt()],
    ids=["cancelled", "keyboard-interrupt"],
)
async def test_cancellation_during_cleanup_is_not_a_cleanup_error(
    tmp_path: Path,
    interrupt: BaseException,
) -> None:
    """cleanup 中の中断を `cleanup-error` に固定しないこと（review 第4次 M3）。

    pre-finalization と同じ理由。ここで確定 status を書くと、中断経路が書こうとする
    `cancelled` が既存 status 保護に阻まれる。
    """

    stop_calls: list[str] = []

    async def interrupting_stop_services() -> None:
        stop_calls.append("stop_services")
        raise interrupt

    runtime = _build_runtime(tmp_path, stop_calls=[])
    runtime.actions.stop_services = interrupting_stop_services  # type: ignore[misc]

    with pytest.raises(type(interrupt)):
        await _service().finalize(runtime, results=_results())

    assert stop_calls == ["stop_services"]
    assert not (tmp_path / "completion_status.json").exists()
    assert not (tmp_path / "completed.flag").exists()


@pytest.mark.asyncio
async def test_a_genuine_cleanup_failure_is_still_a_cleanup_error(tmp_path: Path) -> None:
    """中断以外の cleanup 失敗は従来どおり `cleanup-error` として残すこと。"""

    stop_calls: list[str] = []
    runtime = _build_runtime(tmp_path, stop_calls=stop_calls, fail_stop_services=True)

    with pytest.raises(RuntimeError, match="injected service cleanup failure"):
        await _service().finalize(runtime, results=_results())

    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["termination_reason"] == "cleanup-error"
