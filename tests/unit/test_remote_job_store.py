from __future__ import annotations

import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    ExecutionIdentity,
    GameExecutionResult,
    GameExecutionSpec,
    GameExecutionSpecPayload,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    PlatformArtifactRef,
    RepetitionSpec,
    ResourceRequirements,
    TargetPlatform,
    TimeoutPolicySpec,
    seal_game_execution_spec,
)
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobIdentity,
    RemoteJobState,
    create_remote_job_identity,
)
from shogiarena._core.platform.engine_provisioning import remote_job_supervisor
from shogiarena._core.platform.engine_provisioning.remote_job_store import (
    RemoteJobConflictError,
    RemoteJobStore,
    RemoteJobStoreError,
)


def _spec(*, job_id: str = "logical-job") -> GameExecutionSpec:
    platform = TargetPlatform(operating_system="linux", architecture="x86_64")

    def engine(engine_id: str, digest: str) -> EngineExecutionSpec:
        return EngineExecutionSpec(
            engine_id=engine_id,
            process=EngineProcessSpec(
                artifact=PlatformArtifactRef(
                    logical_id=f"{engine_id}-artifact",
                    kind="engine_binary",
                    sha256=digest * 64,
                    target_platform=platform,
                    entrypoint="engine",
                ),
                working_directory=f"engines/{engine_id}",
                handshake_timeout_ms=10_000,
            ),
            usi=EngineUsiSpec(),
        )

    time_control = GameTimeControlSpec(fixed_time_ms=100)
    return seal_game_execution_spec(
        GameExecutionSpecPayload(
            minimum_worker_version="1.1.0",
            identity=ExecutionIdentity(job_id=job_id, run_id="run", game_id="game-1"),
            black_engine=engine("black", "a"),
            white_engine=engine("white", "b"),
            opening=OpeningSpec(
                initial_sfen="startpos",
                black_engine_id="black",
                white_engine_id="white",
            ),
            time=GameTimeSpec(
                black=time_control,
                white=time_control,
                startup_grace_ms=1_000,
                outer_deadline_ms=10_000,
            ),
            rules=GameRulesSpec(adjudication=AdjudicationSpec(), repetition=RepetitionSpec()),
            timeout=TimeoutPolicySpec(
                watchdog="disabled",
                origin_attribution="best_effort",
                reclassification="disabled",
            ),
            resources=ResourceRequirements(artifact_ids=["black-artifact", "white-artifact"]),
            output=OutputContract(),
        )
    )


def _identity(spec: GameExecutionSpec) -> RemoteJobIdentity:
    return RemoteJobIdentity(
        job_id="job-" + "1" * 32,
        logical_game_id=spec.identity.game_id,
        attempt_id="attempt-" + "2" * 32,
        execution_digest=spec.execution_digest,
    )


def test_prepare_is_idempotent_and_recovers_spec_only_partial_write(tmp_path: Path) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    root.mkdir()
    (root / ".control.lock").write_bytes(b"\0")
    store = RemoteJobStore(root)

    first = store.prepare(identity, spec)
    (root / "status.json").unlink()
    recovered = store.prepare(identity, spec)
    repeated = store.prepare(identity, spec)

    assert first.state == RemoteJobState.CREATED
    assert recovered == repeated
    assert json.loads((root / "spec.json").read_text(encoding="utf-8"))["execution_digest"] == spec.execution_digest


def test_prepare_rejects_same_job_with_different_digest(tmp_path: Path) -> None:
    first_spec = _spec()
    identity = _identity(first_spec)
    store = RemoteJobStore(tmp_path / identity.job_id)
    store.prepare(identity, first_spec)
    different_spec = _spec(job_id="different-logical-job")
    conflicting_identity = identity.model_copy(update={"execution_digest": different_spec.execution_digest})

    with pytest.raises(RemoteJobConflictError, match="different execution digest"):
        store.prepare(conflicting_identity, different_spec)


def test_lifecycle_writes_result_before_terminal_and_acknowledges(tmp_path: Path) -> None:
    spec = _spec()
    identity = _identity(spec)
    store = RemoteJobStore(tmp_path / identity.job_id)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.transition(RemoteJobState.STARTING, process_group_id=123)
    store.transition(RemoteJobState.RUNNING)
    store.heartbeat(1)
    result = GameExecutionResult(
        execution_digest=spec.execution_digest,
        game_id=spec.identity.game_id,
        classification="DRAW_BY_REPETITION",
    )

    terminal = store.finish(terminal_kind="completed", result=result)
    acknowledged = store.acknowledge()

    assert terminal.state == RemoteJobState.TERMINAL
    assert (tmp_path / identity.job_id / "result.json").is_file()
    assert store.read_result().payload.classification == "DRAW_BY_REPETITION"
    assert acknowledged.state == RemoteJobState.ACKNOWLEDGED
    assert acknowledged.acknowledged_at is not None
    cleanup_status = store.record_cleanup_error("temporary directory removal failed")
    assert cleanup_status.terminal_kind == "completed"
    assert cleanup_status.error == "cleanup: temporary directory removal failed"
    assert store.mark_collectable().state == RemoteJobState.COLLECTABLE


def test_result_write_failure_keeps_nonterminal_state_and_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.claim_start()
    store.transition(RemoteJobState.RUNNING)
    result = GameExecutionResult(
        execution_digest=spec.execution_digest,
        game_id=spec.identity.game_id,
        classification="DRAW_BY_REPETITION",
    )
    status_before = (root / "status.json").read_bytes()
    original_atomic_write = RemoteJobStore._atomic_write

    def fail_result_write(path: Path, payload: object) -> None:
        if path.name == "result.json":
            raise OSError(28, "simulated no space left on device")
        original_atomic_write(path, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(RemoteJobStore, "_atomic_write", staticmethod(fail_result_write))
    with pytest.raises(OSError, match="simulated no space left on device"):
        store.finish(terminal_kind="completed", result=result)

    assert (root / "status.json").read_bytes() == status_before
    assert not (root / "result.json").exists()
    assert not list(root.glob(".result.json.*.tmp"))

    monkeypatch.setattr(RemoteJobStore, "_atomic_write", staticmethod(original_atomic_write))
    terminal = store.finish(terminal_kind="completed", result=result)

    assert terminal.state == RemoteJobState.TERMINAL
    assert store.read_result().payload == result


def test_invalid_transition_and_result_digest_fail_closed(tmp_path: Path) -> None:
    spec = _spec()
    identity = _identity(spec)
    store = RemoteJobStore(tmp_path / identity.job_id)
    store.prepare(identity, spec)

    with pytest.raises(RemoteJobStoreError, match="invalid remote job transition"):
        store.transition(RemoteJobState.RUNNING)

    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.CANCELLING)
    wrong_result = GameExecutionResult(
        execution_digest="f" * 64,
        game_id=spec.identity.game_id,
        classification="DRAW_BY_REPETITION",
    )
    with pytest.raises(RemoteJobStoreError, match="non-completed"):
        store.finish(terminal_kind="cancelled", result=wrong_result)


def test_generated_job_and_attempt_ids_are_cryptographically_unique() -> None:
    spec = _spec()

    first = create_remote_job_identity(spec)
    second = create_remote_job_identity(spec)

    assert first.job_id != second.job_id
    assert first.attempt_id != second.attempt_id


def test_same_host_parallel_jobs_use_isolated_directories_and_control_files(
    tmp_path: Path,
) -> None:
    first_spec = _spec(job_id="logical-job-1")
    second_spec = _spec(job_id="logical-job-2")
    first_identity = create_remote_job_identity(first_spec)
    second_identity = create_remote_job_identity(second_spec)
    first_root = tmp_path / "jobs" / first_identity.job_id
    second_root = tmp_path / "jobs" / second_identity.job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            RemoteJobStore(first_root).prepare,
            first_identity,
            first_spec,
        )
        second_future = executor.submit(
            RemoteJobStore(second_root).prepare,
            second_identity,
            second_spec,
        )
        first_future.result()
        second_future.result()

    assert first_root != second_root
    assert (
        json.loads((first_root / "spec.json").read_text(encoding="utf-8"))["execution_digest"]
        == first_spec.execution_digest
    )
    assert (
        json.loads((second_root / "spec.json").read_text(encoding="utf-8"))["execution_digest"]
        == second_spec.execution_digest
    )
    assert (first_root / ".control.lock").is_file()
    assert (second_root / ".control.lock").is_file()


def test_supervisor_prepare_and_start_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    root.mkdir()
    identity_file = root / "request.identity.json"
    spec_file = root / "request.spec.json"
    identity_file.write_text(identity.model_dump_json(), encoding="utf-8")
    spec_file.write_text(spec.model_dump_json(), encoding="utf-8")
    launches: list[list[str]] = []

    class ProcessStub:
        def __init__(self, command: list[str], **_kwargs: object) -> None:
            launches.append(command)

    monkeypatch.setattr(remote_job_supervisor.subprocess, "Popen", ProcessStub)

    assert remote_job_supervisor._prepare(root, identity_file, spec_file) == 0
    assert remote_job_supervisor._start(root, None) == 0
    assert remote_job_supervisor._start(root, None) == 0

    assert len(launches) == 1
    assert launches[0][1:3] == ["-P", "-m"]
    assert RemoteJobStore(root).read_status().state == RemoteJobState.STARTING
    assert not identity_file.exists()
    assert not spec_file.exists()


def test_supervisor_validates_result_before_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.claim_start()
    result = GameExecutionResult(
        execution_digest=spec.execution_digest,
        game_id=spec.identity.game_id,
        classification="DRAW_BY_REPETITION",
    )
    (root / "events.jsonl").write_text(result.model_dump_json() + "\n", encoding="utf-8")

    launches: list[list[str]] = []

    class ProcessStub:
        pid = 321

        def __init__(self, command: list[str], **_kwargs: object) -> None:
            launches.append(command)

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def wait() -> int:
            return 0

    monkeypatch.setattr(remote_job_supervisor.subprocess, "Popen", ProcessStub)

    assert remote_job_supervisor._supervise(root, None) == 0
    assert launches[0][1:3] == ["-P", "-c"]
    assert store.read_status().terminal_kind == "completed"
    assert store.read_result().payload == result


def test_term_grace_escalates_to_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[int] = []
    monotonic_values = iter([0.0, 0.0, 6.0])
    monkeypatch.setattr(remote_job_supervisor.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(remote_job_supervisor.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(remote_job_supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        remote_job_supervisor,
        "_send_group_signal",
        lambda _process_group_id, signal_number: sent.append(int(signal_number)),
    )
    remote_job_supervisor._terminate_process_group(321, grace_sec=5.0)

    assert sent == [int(signal.SIGTERM), 0, 9]


def test_startup_deadline_fails_before_worker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.claim_start()
    secret = root / "secrets.json"
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(remote_job_supervisor, "_age_seconds", lambda _timestamp: 2.0)

    assert remote_job_supervisor._supervise(root, secret) == 1

    status = store.read_status()
    assert status.terminal_kind == "timed_out"
    assert status.error == "worker exceeded startup deadline"
    assert not secret.exists()


def test_status_reports_stale_heartbeat_process_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _spec()
    identity = _identity(spec)
    root = tmp_path / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.transition(RemoteJobState.STARTING)
    store.transition(RemoteJobState.RUNNING, process_group_id=321)
    store.heartbeat(1)
    monkeypatch.setattr(remote_job_supervisor, "_age_seconds", lambda _timestamp: 31.0)
    monkeypatch.setattr(remote_job_supervisor, "_process_group_alive", lambda _process_group_id: True)

    assert remote_job_supervisor._status(root, 30.0) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["process_alive"] is True
    assert payload["heartbeat_stale"] is True
    assert payload["diagnostic"] == "heartbeat is stale while process group is alive"


def test_reaper_retains_then_deletes_acknowledged_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _spec()
    identity = _identity(spec)
    jobs_root = tmp_path / "jobs"
    root = jobs_root / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.CANCELLING)
    store.finish(terminal_kind="cancelled", error="test")
    store.acknowledge()
    lease = tmp_path / "metadata" / "deployments" / ("d" * 64) / "leases" / identity.job_id
    lease.parent.mkdir(parents=True)
    lease.write_text("", encoding="utf-8")
    monkeypatch.setattr(remote_job_supervisor, "_age_seconds", lambda _timestamp: 10.0)

    assert (
        remote_job_supervisor._reap(
            jobs_root,
            heartbeat_stale_sec=30.0,
            retention_sec=5.0,
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert first["collectable"] == 1
    assert first["leases_released"] == 1
    assert not lease.exists()
    assert root.is_dir()

    assert (
        remote_job_supervisor._reap(
            jobs_root,
            heartbeat_stale_sec=30.0,
            retention_sec=5.0,
        )
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["deleted"] == 1
    assert not root.exists()


def test_reaper_deletes_retained_incomplete_job(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    root = jobs_root / ("job-" + "9" * 32)
    root.mkdir(parents=True)
    (root / "partial-input").write_text("incomplete", encoding="utf-8")

    summary = remote_job_supervisor._reap_locked(  # noqa: SLF001
        jobs_root.resolve(),
        heartbeat_stale_sec=30.0,
        retention_sec=0.0,
    )

    assert summary["incomplete"] == 1
    assert summary["deleted"] == 1
    assert not root.exists()


def test_reaper_releases_stale_lease_without_job_directory(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    job_id = "job-" + "8" * 32
    lease = tmp_path / "metadata" / "deployments" / ("d" * 64) / "leases" / job_id
    lease.parent.mkdir(parents=True)
    lease.write_text("", encoding="utf-8")
    old_timestamp = time.time() - 60.0
    os.utime(lease, (old_timestamp, old_timestamp))

    summary = remote_job_supervisor._reap_locked(  # noqa: SLF001
        jobs_root.resolve(),
        heartbeat_stale_sec=30.0,
        retention_sec=0.0,
    )

    assert summary["leases_released"] == 1
    assert not lease.exists()
    assert (lease.parent.parent / "last-used").is_file()


def test_reaper_retains_unacknowledged_terminal_job_lease(tmp_path: Path) -> None:
    spec = _spec()
    identity = _identity(spec)
    jobs_root = tmp_path / "jobs"
    root = jobs_root / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.CANCELLING)
    store.finish(terminal_kind="cancelled", error="awaiting coordinator acknowledgement")
    lease = tmp_path / "metadata" / "deployments" / ("d" * 64) / "leases" / identity.job_id
    lease.parent.mkdir(parents=True)
    lease.write_text("", encoding="utf-8")

    summary = remote_job_supervisor._reap_locked(  # noqa: SLF001
        jobs_root.resolve(),
        heartbeat_stale_sec=30.0,
        retention_sec=0.0,
    )

    assert summary["leases_released"] == 0
    assert lease.is_file()
    assert store.read_status().state == RemoteJobState.TERMINAL


def test_reaper_finishes_orphaned_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = _spec()
    identity = _identity(spec)
    jobs_root = tmp_path / "jobs"
    root = jobs_root / identity.job_id
    store = RemoteJobStore(root)
    store.prepare(identity, spec)
    store.transition(RemoteJobState.PREPARING)
    store.transition(RemoteJobState.PREPARED)
    store.transition(RemoteJobState.STARTING)
    store.transition(RemoteJobState.RUNNING, process_group_id=321)
    store.heartbeat(1)
    monkeypatch.setattr(remote_job_supervisor, "_process_group_alive", lambda _process_group_id: False)

    assert (
        remote_job_supervisor._reap(
            jobs_root,
            heartbeat_stale_sec=30.0,
            retention_sec=5.0,
        )
        == 0
    )
    capsys.readouterr()

    status = store.read_status()
    assert status.terminal_kind == "failed"
    assert "no live process group" in (status.error or "")
