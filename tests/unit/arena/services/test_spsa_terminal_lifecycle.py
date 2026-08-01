from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.spsa.adapters import runner as runner_module
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry


class _Database:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def close(self) -> None:
        self._calls.append("game-db-close")


class _LedgerRuntime:
    def __init__(self, calls: list[str], *, fail_commit: bool = False) -> None:
        self._calls = calls
        self._fail_commit = fail_commit

    def commit_terminal(self, *, status: str, reason: str, resumable: bool) -> None:
        self._calls.append(f"terminal:{status}:{reason}:{resumable}")
        if self._fail_commit:
            raise RuntimeError("terminal commit failed")

    def project_derived_json(self, *, run_dir: Path) -> None:
        del run_dir
        self._calls.append("project")

    def terminal_payload(self) -> dict[str, object]:
        self._calls.append("payload")
        return {
            "schema_version": "shogiarena.spsa.terminal.v1",
            "status": "clean",
            "reason": "completed",
        }

    def completion_status_payload(self, *, cleanup_error: str | None = None) -> dict[str, object]:
        self._calls.append("completion-payload")
        return {
            "schema_version": 1,
            "status": "clean",
            "termination_reason": "completed",
            "cleanup": {"status": "clean", "error": cleanup_error},
        }

    def invalidate_resumable_terminal(self) -> None:
        self._calls.append("ledger-invalidate")


class _Ledger:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def close(self) -> None:
        self._calls.append("ledger-close")


def _runner(tmp_path: Path, *, fail_commit: bool = False) -> tuple[SpsaRunner, list[str]]:
    calls: list[str] = []
    runner = object.__new__(SpsaRunner)
    runner.run_dir = tmp_path
    runner._storage = SimpleNamespace(run_dir=tmp_path)
    runner._state = SpsaRunnerState(
        db_service=_Database(calls),  # type: ignore[arg-type]
        ledger=_Ledger(calls),  # type: ignore[arg-type]
        ledger_runtime=_LedgerRuntime(calls, fail_commit=fail_commit),  # type: ignore[arg-type]
        terminal_status="clean",
        terminal_reason="completed",
        terminal_resumable=False,
    )
    return runner, calls


def test_resume_projection_rebuilds_accepted_best_from_latest_ledger_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = {"update_idx": 3}
    runtime = SimpleNamespace(
        latest_accepted_update_idx=lambda: 3,
        accepted_best_commit=lambda *, update_idx: commit if update_idx == 3 else None,
    )
    runner = object.__new__(SpsaRunner)
    runner._storage = SimpleNamespace(run_dir=tmp_path)
    runner.config = SimpleNamespace(
        baseline=[SimpleNamespace()],
        tuned=[SimpleNamespace()],
        int_rounding="nearest",
        is_snap_float_to_step=False,
    )
    runner._state = SpsaRunnerState(
        ledger_runtime=runtime,  # type: ignore[arg-type]
        params=[
            ParamEntry(
                name="p",
                type="int",
                value=2.0,
                min=1.0,
                max=8.0,
                step=1.0,
                delta=1.0,
                comment="",
                is_not_used=False,
                option_name="USI_P",
            )
        ],
    )
    captured: dict[str, object] = {}

    def _capture(**kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path / "spsa" / "accepted-best.json"

    monkeypatch.setattr(runner_module, "persist_accepted_best", _capture)

    runner._project_accepted_best()

    assert captured["ledger_commit"] == commit
    assert captured["parameter_wire_values"] == {"USI_P": 2}
    assert captured["baseline_engine_count"] == 1
    assert captured["tuned_engine_count"] == 1


def test_accepted_best_projection_failure_does_not_block_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = object.__new__(SpsaRunner)
    runner._storage = SimpleNamespace(run_dir=tmp_path)
    runner.config = SimpleNamespace(
        baseline=[SimpleNamespace()],
        tuned=[SimpleNamespace()],
        int_rounding="nearest",
        is_snap_float_to_step=False,
    )
    runner._state = SpsaRunnerState(
        ledger_runtime=SimpleNamespace(
            latest_accepted_update_idx=lambda: 1,
            accepted_best_commit=lambda *, update_idx: {"update_idx": update_idx},
        ),  # type: ignore[arg-type]
        params=[
            ParamEntry(
                name="p",
                type="int",
                value=2.0,
                min=1.0,
                max=8.0,
                step=1.0,
                delta=1.0,
                comment="",
                is_not_used=False,
                option_name="USI_P",
            )
        ],
    )

    def _fail(**_kwargs: object) -> Path:
        raise ValueError("synthetic projection failure")

    monkeypatch.setattr(runner_module, "persist_accepted_best", _fail)

    runner._project_accepted_best()

    assert "ledger remains authoritative after accepted-best projection failure" in caplog.text


@pytest.mark.asyncio
async def test_terminal_commit_occurs_after_game_db_cleanup_and_before_ledger_close(tmp_path: Path) -> None:
    runner, calls = _runner(tmp_path)

    await runner._stop_additional_services()

    assert calls == [
        "game-db-close",
        "terminal:clean:completed:False",
        "project",
        "payload",
        "completion-payload",
        "ledger-close",
    ]
    payload = json.loads((tmp_path / "spsa" / "terminal.json").read_text(encoding="utf-8"))
    assert payload["reason"] == "completed"
    completion = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert completion["termination_reason"] == "completed"
    assert (tmp_path / "completed.flag").exists()
    assert runner._state.ledger is None


@pytest.mark.asyncio
async def test_terminal_commit_failure_still_closes_ledger(tmp_path: Path) -> None:
    runner, calls = _runner(tmp_path, fail_commit=True)

    with pytest.raises(RuntimeError, match="terminal commit failed"):
        await runner._stop_additional_services()

    assert calls == [
        "game-db-close",
        "terminal:clean:completed:False",
        "ledger-close",
    ]
    assert runner._state.ledger is None


def test_validated_resume_invalidates_only_terminal_artifacts(tmp_path: Path) -> None:
    runner, _calls = _runner(tmp_path)
    terminal_paths = (
        tmp_path / "completion_status.json",
        tmp_path / "completed.flag",
        tmp_path / "spsa" / "terminal.json",
        tmp_path / "spsa" / "terminal.provisional.json",
    )
    for path in terminal_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale", encoding="utf-8")
    preserved = tmp_path / "spsa" / "ledger.sqlite3"
    preserved.write_text("authority", encoding="utf-8")

    runner._invalidate_terminal_artifacts()

    assert all(not path.exists() for path in terminal_paths)
    assert preserved.read_text(encoding="utf-8") == "authority"


@pytest.mark.asyncio
async def test_resumable_terminal_is_invalidated_at_dispatch_boundary(tmp_path: Path) -> None:
    runner, calls = _runner(tmp_path)
    runner._state.has_resumable_terminal = True
    completion = tmp_path / "completion_status.json"
    completion.write_text("stale", encoding="utf-8")

    class _Controller:
        async def run_orchestrator(self, _orchestrator: object, run_coro: object) -> None:
            del run_coro
            calls.append("dispatch")

    runner._run_controller = _Controller()

    await runner.run_orchestrator(SimpleNamespace(), SimpleNamespace())

    assert calls[-2:] == ["ledger-invalidate", "dispatch"]
    assert not completion.exists()
    assert runner._state.has_resumable_terminal is False


@pytest.mark.asyncio
async def test_artifact_invalidation_failure_keeps_ledger_resume_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, calls = _runner(tmp_path)
    runner._state.has_resumable_terminal = True
    monkeypatch.setattr(
        runner,
        "_invalidate_terminal_artifacts",
        lambda: (_ for _ in ()).throw(OSError("unlink failed")),
    )

    with pytest.raises(OSError, match="unlink failed"):
        await runner.run_orchestrator(SimpleNamespace(), SimpleNamespace())

    assert "ledger-invalidate" not in calls
    assert runner._state.has_resumable_terminal is True


@pytest.mark.asyncio
async def test_result_persistence_failure_cannot_publish_clean_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shogiarena._core.contexts.spsa.adapters.runner as runner_module

    runner, calls = _runner(tmp_path)
    runner.config = SimpleNamespace(num_updates=1)

    class _FailingResultStore:
        def save_result(self, _result: object) -> None:
            calls.append("result-save")
            raise OSError("result persistence failed")

    runner._result_store = _FailingResultStore()
    monkeypatch.setattr(
        runner_module,
        "build_spsa_final_result",
        lambda **_kwargs: SimpleNamespace(run_id="run-1"),
    )

    with pytest.raises(OSError, match="result persistence failed"):
        await runner.finalize_and_persist(None)

    assert calls == ["result-save"]
    assert runner._state.terminal_status == "failed"
    assert runner._state.terminal_reason == "failed"
    status = json.loads((tmp_path / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["is_provisional"] is True
    assert not (tmp_path / "completed.flag").exists()
