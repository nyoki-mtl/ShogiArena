"""Agentless durable Remote job process supervisor."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
    GameExecutionSpec,
)
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobIdentity,
    RemoteJobState,
    RemoteJobStatus,
)
from shogiarena._core.platform.engine_provisioning.remote_job_store import (
    RemoteJobStore,
    RemoteJobStoreError,
    remote_file_lock,
)

_POLL_INTERVAL_SEC = 0.1
_HEARTBEAT_INTERVAL_SEC = 1.0
_TERM_GRACE_SEC = 5.0
_HEARTBEAT_STALE_SEC = 30.0
_RETENTION_SEC = 86_400.0
_JOB_DIRECTORY_PATTERN = re.compile(r"^job-[0-9a-f]{32}$")
_DEPLOYMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def main(argv: list[str] | None = None) -> int:
    """Run one internal durable-job control command."""

    parser = argparse.ArgumentParser(prog="shogiarena-remote-job")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--job-root", type=Path, required=True)
    prepare.add_argument("--identity-file", type=Path, required=True)
    prepare.add_argument("--spec-file", type=Path, required=True)

    start = commands.add_parser("start")
    start.add_argument("--job-root", type=Path, required=True)
    start.add_argument("--secret-file", type=Path)

    supervise = commands.add_parser("supervise")
    supervise.add_argument("--job-root", type=Path, required=True)
    supervise.add_argument("--secret-file", type=Path)

    status = commands.add_parser("status")
    status.add_argument("--job-root", type=Path, required=True)
    status.add_argument("--heartbeat-stale-sec", type=float, default=_HEARTBEAT_STALE_SEC)

    result = commands.add_parser("result")
    result.add_argument("--job-root", type=Path, required=True)

    cancel = commands.add_parser("cancel")
    cancel.add_argument("--job-root", type=Path, required=True)
    cancel.add_argument("--term-grace-sec", type=float, default=_TERM_GRACE_SEC)

    acknowledge = commands.add_parser("ack")
    acknowledge.add_argument("--job-root", type=Path, required=True)

    reap = commands.add_parser("reap")
    reap.add_argument("--jobs-root", type=Path, required=True)
    reap.add_argument("--heartbeat-stale-sec", type=float, default=_HEARTBEAT_STALE_SEC)
    reap.add_argument("--retention-sec", type=float, default=_RETENTION_SEC)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return _prepare(args.job_root, args.identity_file, args.spec_file)
        if args.command == "start":
            return _start(args.job_root, args.secret_file)
        if args.command == "supervise":
            return _supervise(args.job_root, args.secret_file)
        if args.command == "status":
            return _status(args.job_root, args.heartbeat_stale_sec)
        if args.command == "result":
            return _result(args.job_root)
        if args.command == "cancel":
            return _cancel(args.job_root, args.term_grace_sec)
        if args.command == "ack":
            return _acknowledge(args.job_root)
        if args.command == "reap":
            return _reap(
                args.jobs_root,
                heartbeat_stale_sec=args.heartbeat_stale_sec,
                retention_sec=args.retention_sec,
            )
    except (OSError, ValueError, RemoteJobStoreError, ValidationError) as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 2
    raise AssertionError(f"unsupported remote job command: {args.command}")


def _prepare(job_root: Path, identity_file: Path, spec_file: Path) -> int:
    identity = RemoteJobIdentity.model_validate_json(identity_file.read_text(encoding="utf-8"))
    spec = GameExecutionSpec.model_validate_json(spec_file.read_text(encoding="utf-8"))
    store = RemoteJobStore(job_root)
    status = store.prepare(identity, spec)
    if status.state == RemoteJobState.CREATED:
        status = store.transition(RemoteJobState.PREPARING)
        status = store.transition(RemoteJobState.PREPARED)
    resolved_root = job_root.resolve(strict=True)
    for request_file, expected_name in (
        (identity_file, "request.identity.json"),
        (spec_file, "request.spec.json"),
    ):
        resolved_request = request_file.resolve(strict=True)
        if resolved_request.parent == resolved_root and resolved_request.name == expected_name:
            resolved_request.unlink()
    _emit(status.model_dump(mode="json"))
    return 0


def _start(job_root: Path, secret_file: Path | None) -> int:
    store = RemoteJobStore(job_root)
    status, is_claimed = store.claim_start()
    if not is_claimed:
        _emit(status.model_dump(mode="json"))
        return 0
    command = [
        sys.executable,
        "-P",
        "-m",
        "shogiarena._core.platform.engine_provisioning.remote_job_supervisor",
        "supervise",
        "--job-root",
        str(job_root),
    ]
    if secret_file is not None:
        command.extend(["--secret-file", str(secret_file)])
    supervisor_log = (job_root / "supervisor.log").open("ab")
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=supervisor_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except BaseException as exc:
        store.transition(RemoteJobState.CANCELLING)
        store.finish(terminal_kind="failed", error=f"failed to start supervisor: {exc}")
        raise
    finally:
        supervisor_log.close()
    _emit(store.read_status().model_dump(mode="json"))
    return 0


def _supervise(job_root: Path, secret_file: Path | None) -> int:
    store = RemoteJobStore(job_root)
    spec = GameExecutionSpec.model_validate_json((job_root / "spec.json").read_text(encoding="utf-8"))
    status = store.read_status()
    if status.state != RemoteJobState.STARTING:
        if status.state in {
            RemoteJobState.RUNNING,
            RemoteJobState.CANCELLING,
            RemoteJobState.TERMINAL,
            RemoteJobState.ACKNOWLEDGED,
            RemoteJobState.COLLECTABLE,
        }:
            return 0
        raise RemoteJobStoreError(f"cannot supervise remote job in state {status.state}")
    try:
        return _run_supervised_worker(job_root, store, spec, secret_file)
    finally:
        if secret_file is not None:
            try:
                secret_file.unlink(missing_ok=True)
            except OSError as exc:
                try:
                    store.record_cleanup_error(f"failed to remove secret file: {exc}")
                except (OSError, ValueError, RemoteJobStoreError):
                    pass


def _run_supervised_worker(
    job_root: Path,
    store: RemoteJobStore,
    spec: GameExecutionSpec,
    secret_file: Path | None,
) -> int:
    startup_elapsed = _age_seconds(store.read_status().updated_at)
    if startup_elapsed > spec.time.startup_grace_ms / 1000:
        _begin_cancelling(store)
        store.finish(terminal_kind="timed_out", error="worker exceeded startup deadline")
        return 1
    environment = os.environ.copy()
    if secret_file is not None:
        environment["SHOGIARENA_ENGINE_SECRET_BUNDLE_FILE"] = str(secret_file)
    command = [
        sys.executable,
        "-P",
        "-c",
        "from shogiarena.cli import main; main()",
        "_internal",
        "remote-run-pair",
        "--spec-file",
        str(job_root / "spec.json"),
    ]
    events_path = job_root / "events.jsonl"
    worker_log_path = job_root / "worker.log"
    with events_path.open("ab") as events, worker_log_path.open("ab") as worker_log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=events,
            stderr=worker_log,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
    if _age_seconds(store.read_status().updated_at) > spec.time.startup_grace_ms / 1000:
        _begin_cancelling(store)
        _terminate_process_group(process.pid, grace_sec=_TERM_GRACE_SEC)
        process.wait()
        store.finish(terminal_kind="timed_out", error="worker exceeded startup deadline")
        return 1
    store.transition(RemoteJobState.RUNNING, process_group_id=process.pid)
    deadline = time.monotonic() + spec.time.outer_deadline_ms / 1000
    next_heartbeat = 0.0
    sequence = 0
    did_time_out = False
    while process.poll() is None:
        now = time.monotonic()
        if now >= deadline:
            did_time_out = True
            _begin_cancelling(store)
            _terminate_process_group(process.pid, grace_sec=_TERM_GRACE_SEC)
            break
        if now >= next_heartbeat:
            sequence += 1
            store.heartbeat(sequence)
            next_heartbeat = now + _HEARTBEAT_INTERVAL_SEC
        time.sleep(min(_POLL_INTERVAL_SEC, max(0.0, deadline - now)))
    return_code = process.wait()
    current = store.read_status()
    if did_time_out:
        store.finish(terminal_kind="timed_out", error="worker exceeded outer deadline")
        return 1
    if current.state == RemoteJobState.CANCELLING:
        store.finish(terminal_kind="cancelled", error="cancelled by coordinator")
        return 1
    if return_code != 0:
        _begin_cancelling(store)
        store.finish(terminal_kind="failed", error=f"worker exited with code {return_code}")
        return 1
    try:
        result = _read_worker_result(events_path)
    except RemoteJobStoreError as exc:
        _begin_cancelling(store)
        store.finish(terminal_kind="failed", error=str(exc))
        return 1
    store.finish(terminal_kind="completed", result=result)
    return 0


def _status(job_root: Path, heartbeat_stale_sec: float) -> int:
    if heartbeat_stale_sec <= 0:
        raise ValueError("heartbeat stale threshold must be positive")
    store = RemoteJobStore(job_root)
    status = store.read_status()
    if status.state in {RemoteJobState.STARTING, RemoteJobState.RUNNING, RemoteJobState.CANCELLING}:
        alive = _process_group_alive(status.process_group_id) if status.process_group_id is not None else None
        heartbeat = store.read_heartbeat()
        heartbeat_age = (
            _age_seconds(heartbeat.observed_at) if heartbeat is not None else _age_seconds(status.updated_at)
        )
        stale = heartbeat_age > heartbeat_stale_sec
        diagnostic: str | None = None
        if status.state == RemoteJobState.STARTING and status.process_group_id is None:
            diagnostic = "worker process group has not been published"
        elif stale and alive:
            diagnostic = "heartbeat is stale while process group is alive"
        elif alive is False:
            diagnostic = "process group is absent before terminal status"
        status = status.model_copy(
            update={
                "process_alive": alive,
                "heartbeat_stale": stale,
                "diagnostic": diagnostic,
            }
        )
    _emit(status.model_dump(mode="json"))
    return 0


def _result(job_root: Path) -> int:
    _emit(RemoteJobStore(job_root).read_result().model_dump(mode="json"))
    return 0


def _cancel(job_root: Path, term_grace_sec: float) -> int:
    if term_grace_sec < 0:
        raise ValueError("term grace must be non-negative")
    store = RemoteJobStore(job_root)
    status = store.read_status()
    if status.state in {
        RemoteJobState.TERMINAL,
        RemoteJobState.ACKNOWLEDGED,
        RemoteJobState.COLLECTABLE,
    }:
        _emit(status.model_dump(mode="json"))
        return 0
    _begin_cancelling(store)
    status = store.read_status()
    if status.process_group_id is None:
        status = store.finish(terminal_kind="cancelled", error="cancelled before worker process start")
    else:
        _terminate_process_group(status.process_group_id, grace_sec=term_grace_sec)
        deadline = time.monotonic() + term_grace_sec + 1.0
        while time.monotonic() < deadline:
            status = store.read_status()
            if status.state == RemoteJobState.TERMINAL:
                break
            time.sleep(_POLL_INTERVAL_SEC)
    _emit(status.model_dump(mode="json"))
    return 0


def _acknowledge(job_root: Path) -> int:
    _emit(RemoteJobStore(job_root).acknowledge().model_dump(mode="json"))
    return 0


def _reap(
    jobs_root: Path,
    *,
    heartbeat_stale_sec: float,
    retention_sec: float,
) -> int:
    if heartbeat_stale_sec <= 0:
        raise ValueError("heartbeat stale threshold must be positive")
    if retention_sec < 0:
        raise ValueError("retention must be non-negative")
    jobs_root.mkdir(parents=True, exist_ok=True)
    jobs_root = jobs_root.resolve(strict=True)
    with remote_file_lock(jobs_root / ".reaper.lock"):
        summary = _reap_locked(
            jobs_root,
            heartbeat_stale_sec=heartbeat_stale_sec,
            retention_sec=retention_sec,
        )
    _emit(summary)
    return 0


def _reap_locked(
    jobs_root: Path,
    *,
    heartbeat_stale_sec: float,
    retention_sec: float,
) -> dict[str, int]:
    summary = {
        "failed": 0,
        "timed_out": 0,
        "collectable": 0,
        "deleted": 0,
        "incomplete": 0,
        "leases_released": 0,
        "retained": 0,
    }
    for job_root in sorted(jobs_root.iterdir()):
        if not _JOB_DIRECTORY_PATTERN.fullmatch(job_root.name) or job_root.is_symlink() or not job_root.is_dir():
            continue
        store = RemoteJobStore(job_root)
        reaper_finished_job = False
        if not (job_root / "status.json").is_file():
            summary["incomplete"] += 1
            if max(0.0, time.time() - job_root.stat().st_mtime) >= retention_sec:
                summary["leases_released"] += _release_deployment_leases(jobs_root, job_root.name)
                _remove_job_tree(jobs_root, job_root)
                summary["deleted"] += 1
            else:
                summary["retained"] += 1
            continue
        status = store.read_status()
        if status.state == RemoteJobState.STARTING:
            spec = GameExecutionSpec.model_validate_json((job_root / "spec.json").read_text(encoding="utf-8"))
            if _age_seconds(status.updated_at) > spec.time.startup_grace_ms / 1000:
                _begin_cancelling(store)
                store.finish(terminal_kind="timed_out", error="orphaned worker exceeded startup deadline")
                summary["timed_out"] += 1
                status = store.read_status()
                reaper_finished_job = True
        elif status.state in {RemoteJobState.RUNNING, RemoteJobState.CANCELLING}:
            heartbeat = store.read_heartbeat()
            heartbeat_stale = (
                _age_seconds(heartbeat.observed_at) if heartbeat is not None else _age_seconds(status.updated_at)
            ) > heartbeat_stale_sec
            process_alive = _process_group_alive(status.process_group_id)
            if not process_alive:
                _begin_cancelling(store)
                terminal_kind = "cancelled" if status.state == RemoteJobState.CANCELLING else "failed"
                store.finish(
                    terminal_kind=terminal_kind,
                    error="orphan reaper found no live process group before terminal status",
                )
                summary["failed"] += 1
                status = store.read_status()
                reaper_finished_job = True
            elif heartbeat_stale and status.process_group_id is not None:
                _begin_cancelling(store)
                _terminate_process_group(status.process_group_id, grace_sec=_TERM_GRACE_SEC)
                store.finish(
                    terminal_kind="failed",
                    error="orphan reaper terminated a live process group with a stale heartbeat",
                )
                summary["failed"] += 1
                status = store.read_status()
                reaper_finished_job = True
        if reaper_finished_job and status.state == RemoteJobState.TERMINAL:
            status = store.acknowledge()
        if status.state in {
            RemoteJobState.TERMINAL,
            RemoteJobState.ACKNOWLEDGED,
            RemoteJobState.COLLECTABLE,
        }:
            try:
                (job_root / "secrets.json").unlink(missing_ok=True)
            except OSError as exc:
                store.record_cleanup_error(f"failed to remove secret file during reap: {exc}")
        if status.state in {
            RemoteJobState.ACKNOWLEDGED,
            RemoteJobState.COLLECTABLE,
        }:
            summary["leases_released"] += _release_deployment_leases(jobs_root, status.identity.job_id)
        if status.state == RemoteJobState.ACKNOWLEDGED and status.acknowledged_at is not None:
            if _age_seconds(status.acknowledged_at) >= retention_sec:
                status = store.mark_collectable()
                summary["collectable"] += 1
        elif status.state == RemoteJobState.COLLECTABLE:
            _remove_collectable_job(jobs_root, job_root, status)
            summary["deleted"] += 1
            continue
        summary["retained"] += 1
    summary["leases_released"] += _release_missing_job_leases(
        jobs_root,
        retention_sec=retention_sec,
    )
    return summary


def _begin_cancelling(store: RemoteJobStore) -> None:
    status = store.read_status()
    if status.state not in {
        RemoteJobState.CANCELLING,
        RemoteJobState.TERMINAL,
        RemoteJobState.ACKNOWLEDGED,
        RemoteJobState.COLLECTABLE,
    }:
        store.transition(RemoteJobState.CANCELLING)


def _terminate_process_group(process_group_id: int, *, grace_sec: float) -> None:
    try:
        _send_group_signal(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_sec
    while time.monotonic() < deadline:
        try:
            _send_group_signal(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(_POLL_INTERVAL_SEC)
    try:
        sigkill = getattr(signal, "SIGKILL", None)
        if sigkill is None:
            raise RuntimeError("SIGKILL is unavailable on this worker platform")
        _send_group_signal(process_group_id, sigkill)
    except ProcessLookupError:
        return


def _send_group_signal(process_group_id: int, signal_number: int) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        raise RuntimeError("process-group signaling is unavailable on this worker platform")
    killpg(process_group_id, signal_number)


def _process_group_alive(process_group_id: int | None) -> bool:
    if process_group_id is None:
        return False
    try:
        _send_group_signal(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_collectable_job(
    jobs_root: Path,
    job_root: Path,
    status: RemoteJobStatus,
) -> None:
    if _process_group_alive(status.process_group_id):
        raise RemoteJobStoreError(f"refusing to clean job with live process group: {job_root}")
    _remove_job_tree(jobs_root, job_root)


def _remove_job_tree(jobs_root: Path, job_root: Path) -> None:
    resolved = job_root.resolve(strict=True)
    if resolved.parent != jobs_root or not _JOB_DIRECTORY_PATTERN.fullmatch(resolved.name):
        raise RemoteJobStoreError(f"refusing unsafe remote job cleanup target: {job_root}")
    shutil.rmtree(resolved)


def _release_deployment_leases(jobs_root: Path, job_id: str) -> int:
    if not _JOB_DIRECTORY_PATTERN.fullmatch(job_id):
        raise RemoteJobStoreError(f"refusing invalid deployment lease job ID: {job_id}")
    metadata_root = jobs_root.parent / "metadata" / "deployments"
    if not metadata_root.is_dir() or metadata_root.is_symlink():
        return 0
    released = 0
    locks_root = jobs_root.parent / "locks" / "deployments"
    for deployment_root in metadata_root.iterdir():
        if (
            not _DEPLOYMENT_ID_PATTERN.fullmatch(deployment_root.name)
            or deployment_root.is_symlink()
            or not deployment_root.is_dir()
        ):
            continue
        lock = locks_root / f"{deployment_root.name}.lock"
        with remote_file_lock(lock):
            leases_root = deployment_root / "leases"
            if not leases_root.is_dir() or leases_root.is_symlink():
                continue
            lease = leases_root / job_id
            if lease.is_file() or lease.is_symlink():
                lease.unlink()
                _refresh_deployment_last_used(deployment_root)
                released += 1
    return released


def _release_missing_job_leases(jobs_root: Path, *, retention_sec: float) -> int:
    metadata_root = jobs_root.parent / "metadata" / "deployments"
    if not metadata_root.is_dir() or metadata_root.is_symlink():
        return 0
    released = 0
    locks_root = jobs_root.parent / "locks" / "deployments"
    stale_after_sec = max(retention_sec, _HEARTBEAT_STALE_SEC)
    for deployment_root in metadata_root.iterdir():
        if (
            not _DEPLOYMENT_ID_PATTERN.fullmatch(deployment_root.name)
            or deployment_root.is_symlink()
            or not deployment_root.is_dir()
        ):
            continue
        lock = locks_root / f"{deployment_root.name}.lock"
        with remote_file_lock(lock):
            leases_root = deployment_root / "leases"
            if not leases_root.is_dir() or leases_root.is_symlink():
                continue
            for lease in leases_root.iterdir():
                if not _JOB_DIRECTORY_PATTERN.fullmatch(lease.name):
                    continue
                if (jobs_root / lease.name).exists():
                    continue
                if max(0.0, time.time() - lease.lstat().st_mtime) < stale_after_sec:
                    continue
                lease.unlink()
                _refresh_deployment_last_used(deployment_root)
                released += 1
    return released


def _refresh_deployment_last_used(deployment_root: Path) -> None:
    (deployment_root / "last-used").write_text(str(int(time.time())), encoding="ascii")


def _age_seconds(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError("remote job timestamp must include a timezone")
    return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())


def _read_worker_result(events_path: Path) -> GameExecutionResult:
    result: GameExecutionResult | None = None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
            candidate = GameExecutionResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            continue
        result = candidate
    if result is None:
        raise RemoteJobStoreError("worker completed without a valid GameExecutionResult")
    return result


def _emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
