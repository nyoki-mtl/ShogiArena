"""Atomic filesystem storage for durable Remote jobs."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
    GameExecutionSpec,
)
from shogiarena._core.contexts.game_session.ports.remote_job import (
    RemoteJobHeartbeat,
    RemoteJobIdentity,
    RemoteJobResult,
    RemoteJobState,
    RemoteJobStatus,
)
from shogiarena._core.shared.kernel.hash_normalization import HashInput
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_json_bytes

_TRANSITIONS: dict[RemoteJobState, frozenset[RemoteJobState]] = {
    RemoteJobState.CREATED: frozenset({RemoteJobState.PREPARING}),
    RemoteJobState.PREPARING: frozenset({RemoteJobState.PREPARED, RemoteJobState.CANCELLING}),
    RemoteJobState.PREPARED: frozenset({RemoteJobState.STARTING, RemoteJobState.CANCELLING}),
    RemoteJobState.STARTING: frozenset({RemoteJobState.RUNNING, RemoteJobState.CANCELLING}),
    RemoteJobState.RUNNING: frozenset({RemoteJobState.CANCELLING, RemoteJobState.TERMINAL}),
    RemoteJobState.CANCELLING: frozenset({RemoteJobState.TERMINAL}),
    RemoteJobState.TERMINAL: frozenset({RemoteJobState.ACKNOWLEDGED}),
    RemoteJobState.ACKNOWLEDGED: frozenset({RemoteJobState.COLLECTABLE}),
    RemoteJobState.COLLECTABLE: frozenset(),
}
ModelT = TypeVar("ModelT", bound=BaseModel)


class RemoteJobStoreError(RuntimeError):
    """Durable job storage contract violation."""


class RemoteJobConflictError(RemoteJobStoreError):
    """Same job ID was reused with different immutable inputs."""


class RemoteJobStore:
    """Own one per-job directory and its atomic lifecycle artifacts."""

    def __init__(self, job_root: Path) -> None:
        self._root = job_root
        self._spec_path = job_root / "spec.json"
        self._status_path = job_root / "status.json"
        self._heartbeat_path = job_root / "heartbeat.json"
        self._result_path = job_root / "result.json"
        self._lock_path = job_root / ".control.lock"

    def prepare(self, identity: RemoteJobIdentity, spec: GameExecutionSpec) -> RemoteJobStatus:
        """Create the job idempotently or return its existing status."""

        if identity.execution_digest != spec.execution_digest:
            raise RemoteJobConflictError("job identity execution digest does not match GameExecutionSpec")
        if identity.logical_game_id != spec.identity.game_id:
            raise RemoteJobConflictError("job logical game ID does not match GameExecutionSpec")
        if self._root.name != identity.job_id:
            raise RemoteJobConflictError("job directory name does not match job identity")
        self._root.mkdir(parents=True, exist_ok=True)
        with self._control_lock():
            existing_spec = self._read_optional_model(self._spec_path, GameExecutionSpec)
            if existing_spec is not None and existing_spec.execution_digest != spec.execution_digest:
                raise RemoteJobConflictError("same job path already contains a different execution digest")
            if existing_spec is None:
                self._atomic_write(self._spec_path, spec.model_dump(mode="json"))

            existing_status = self._read_optional_model(self._status_path, RemoteJobStatus)
            if existing_status is not None:
                if existing_status.identity != identity:
                    raise RemoteJobConflictError("same job path already contains a different job identity")
                return existing_status

            status = RemoteJobStatus(
                identity=identity,
                state=RemoteJobState.CREATED,
                revision=0,
                updated_at=_utc_now(),
            )
            self._atomic_write(self._status_path, status.model_dump(mode="json"))
            return status

    def read_status(self) -> RemoteJobStatus:
        """Read and validate current durable status."""

        return self._read_required_model(self._status_path, RemoteJobStatus)

    def read_heartbeat(self) -> RemoteJobHeartbeat | None:
        """Read the latest heartbeat when one has been published."""

        return self._read_optional_model(self._heartbeat_path, RemoteJobHeartbeat)

    def transition(
        self,
        state: RemoteJobState,
        *,
        process_group_id: int | None = None,
    ) -> RemoteJobStatus:
        """Apply one allowed lifecycle transition atomically."""

        with self._control_lock():
            current = self.read_status()
            if state == current.state:
                return current
            if state in {
                RemoteJobState.TERMINAL,
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                raise RemoteJobStoreError("terminal and acknowledgement transitions require dedicated operations")
            if state not in _TRANSITIONS[current.state]:
                raise RemoteJobStoreError(f"invalid remote job transition: {current.state} -> {state}")
            updated = RemoteJobStatus.model_validate(
                current.model_copy(
                    update={
                        "state": state,
                        "revision": current.revision + 1,
                        "updated_at": _utc_now(),
                        "process_group_id": process_group_id or current.process_group_id,
                    }
                ).model_dump(mode="json")
            )
            self._atomic_write(self._status_path, updated.model_dump(mode="json"))
            return updated

    def heartbeat(self, sequence: int) -> RemoteJobHeartbeat:
        """Persist a monotonic heartbeat for a running job."""

        with self._control_lock():
            status = self.read_status()
            if status.state not in {RemoteJobState.RUNNING, RemoteJobState.CANCELLING}:
                raise RemoteJobStoreError(f"cannot heartbeat remote job in state {status.state}")
            existing = self._read_optional_model(self._heartbeat_path, RemoteJobHeartbeat)
            if existing is not None and sequence <= existing.sequence:
                raise RemoteJobStoreError("heartbeat sequence must increase monotonically")
            heartbeat = RemoteJobHeartbeat(
                job_id=status.identity.job_id,
                execution_digest=status.identity.execution_digest,
                sequence=sequence,
                observed_at=_utc_now(),
            )
            self._atomic_write(self._heartbeat_path, heartbeat.model_dump(mode="json"))
            return heartbeat

    def claim_start(self) -> tuple[RemoteJobStatus, bool]:
        """Atomically claim PREPARED -> STARTING for exactly one launcher."""

        with self._control_lock():
            current = self.read_status()
            if current.state == RemoteJobState.PREPARED:
                updated = RemoteJobStatus.model_validate(
                    current.model_copy(
                        update={
                            "state": RemoteJobState.STARTING,
                            "revision": current.revision + 1,
                            "updated_at": _utc_now(),
                        }
                    ).model_dump(mode="json")
                )
                self._atomic_write(self._status_path, updated.model_dump(mode="json"))
                return updated, True
            if current.state in {
                RemoteJobState.STARTING,
                RemoteJobState.RUNNING,
                RemoteJobState.CANCELLING,
                RemoteJobState.TERMINAL,
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                return current, False
            raise RemoteJobStoreError(f"cannot start remote job in state {current.state}")

    def finish(
        self,
        *,
        terminal_kind: Literal["completed", "failed", "timed_out", "cancelled"],
        result: GameExecutionResult | None = None,
        error: str | None = None,
    ) -> RemoteJobStatus:
        """Write validated result first, then publish TERMINAL status."""

        with self._control_lock():
            current = self.read_status()
            if current.state == RemoteJobState.TERMINAL:
                if current.terminal_kind != terminal_kind:
                    raise RemoteJobConflictError("remote job already terminated with a different terminal kind")
                return current
            if RemoteJobState.TERMINAL not in _TRANSITIONS[current.state]:
                raise RemoteJobStoreError(f"cannot finish remote job in state {current.state}")
            result_envelope: RemoteJobResult | None = None
            if terminal_kind == "completed":
                if result is None:
                    raise RemoteJobStoreError("completed remote job requires a result")
                if result.execution_digest != current.identity.execution_digest:
                    raise RemoteJobConflictError("result execution digest does not match job identity")
                if result.game_id != current.identity.logical_game_id:
                    raise RemoteJobConflictError("result game ID does not match job identity")
                result_envelope = RemoteJobResult(
                    job_id=current.identity.job_id,
                    execution_digest=result.execution_digest,
                    logical_game_id=result.game_id,
                    payload=result,
                )
            elif result is not None:
                raise RemoteJobStoreError("non-completed remote job must not contain a result")
            updated = current.model_copy(
                update={
                    "state": RemoteJobState.TERMINAL,
                    "revision": current.revision + 1,
                    "updated_at": _utc_now(),
                    "terminal_kind": terminal_kind,
                    "error": error,
                }
            )
            validated = RemoteJobStatus.model_validate(updated.model_dump(mode="json"))
            if result_envelope is not None:
                self._atomic_write(self._result_path, result_envelope.model_dump(mode="json"))
            self._atomic_write(self._status_path, validated.model_dump(mode="json"))
            return validated

    def acknowledge(self) -> RemoteJobStatus:
        """Record coordinator acknowledgement idempotently."""

        with self._control_lock():
            current = self.read_status()
            if current.state in {RemoteJobState.ACKNOWLEDGED, RemoteJobState.COLLECTABLE}:
                return current
            if current.state != RemoteJobState.TERMINAL:
                raise RemoteJobStoreError(f"cannot acknowledge remote job in state {current.state}")
            updated = current.model_copy(
                update={
                    "state": RemoteJobState.ACKNOWLEDGED,
                    "revision": current.revision + 1,
                    "updated_at": _utc_now(),
                    "acknowledged_at": _utc_now(),
                }
            )
            self._atomic_write(self._status_path, updated.model_dump(mode="json"))
            return updated

    def mark_collectable(self) -> RemoteJobStatus:
        """Mark an acknowledged job as eligible for retention cleanup."""

        with self._control_lock():
            current = self.read_status()
            if current.state == RemoteJobState.COLLECTABLE:
                return current
            if current.state != RemoteJobState.ACKNOWLEDGED:
                raise RemoteJobStoreError(f"cannot mark remote job collectable in state {current.state}")
            updated = current.model_copy(
                update={
                    "state": RemoteJobState.COLLECTABLE,
                    "revision": current.revision + 1,
                    "updated_at": _utc_now(),
                }
            )
            validated = RemoteJobStatus.model_validate(updated.model_dump(mode="json"))
            self._atomic_write(self._status_path, validated.model_dump(mode="json"))
            return validated

    def record_cleanup_error(self, message: str) -> RemoteJobStatus:
        """Append cleanup diagnostics without changing the primary terminal verdict."""

        normalized = message.strip()
        if not normalized:
            raise ValueError("cleanup error message must not be empty")
        with self._control_lock():
            current = self.read_status()
            if current.state not in {
                RemoteJobState.TERMINAL,
                RemoteJobState.ACKNOWLEDGED,
                RemoteJobState.COLLECTABLE,
            }:
                raise RemoteJobStoreError(f"cannot record cleanup error in remote job state {current.state}")
            error = f"{current.error}; cleanup: {normalized}" if current.error else f"cleanup: {normalized}"
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "updated_at": _utc_now(),
                    "error": error,
                }
            )
            validated = RemoteJobStatus.model_validate(updated.model_dump(mode="json"))
            self._atomic_write(self._status_path, validated.model_dump(mode="json"))
            return validated

    def read_result(self) -> RemoteJobResult:
        """Read a validated terminal result envelope."""

        result = self._read_required_model(self._result_path, RemoteJobResult)
        status = self.read_status()
        if result.job_id != status.identity.job_id or result.execution_digest != status.identity.execution_digest:
            raise RemoteJobConflictError("result envelope does not match current job status")
        game_result = result.payload
        if game_result.game_id != result.logical_game_id:
            raise RemoteJobConflictError("result payload game ID does not match result envelope")
        return result

    @contextmanager
    def _control_lock(self) -> Iterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        with remote_file_lock(self._lock_path):
            yield

    @staticmethod
    def _atomic_write(path: Path, payload: HashInput) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(canonical_json_bytes(payload))
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_optional_model(
        path: Path,
        model: type[ModelT],
    ) -> ModelT | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return model.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise RemoteJobStoreError(f"invalid remote job artifact: {path}: {exc}") from exc

    @classmethod
    def _read_required_model(cls, path: Path, model: type[ModelT]) -> ModelT:
        value = cls._read_optional_model(path, model)
        if value is None:
            raise RemoteJobStoreError(f"required remote job artifact is missing: {path}")
        return value


def _lock_file(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise BlockingIOError from exc


def _unlock_file(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def remote_file_lock(path: Path, *, timeout_sec: float = 5.0) -> Iterator[None]:
    """Acquire a crash-safe advisory file lock on the worker filesystem."""

    if timeout_sec <= 0:
        raise ValueError("file lock timeout must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    with path.open("a+b") as handle:
        while True:
            try:
                _lock_file(handle.fileno())
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RemoteJobStoreError(f"timed out acquiring remote file lock: {path}") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock_file(handle.fileno())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "RemoteJobConflictError",
    "RemoteJobStore",
    "RemoteJobStoreError",
    "remote_file_lock",
]
