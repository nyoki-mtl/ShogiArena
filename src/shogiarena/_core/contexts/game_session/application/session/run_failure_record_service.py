"""Persist and load structured run failure records."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import BinaryIO

from shogiarena._core.contexts.game_session.domain.failure_records import (
    FailurePhase,
    RunFailureRecord,
    coerce_failure_phase,
)
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str

logger = logging.getLogger(__name__)

_FAILURES_JSON = "run_failures.json"
_FAILURES_JSONL = "run_failures.jsonl"
_STARTUP_FAILURE_JSON = "engine_startup_failure.json"
_FAILURES_DIR = "failures"
_APPEND_LOCK = threading.Lock()


class RunFailureRecordService:
    """run failure records を JSON アーティファクトとして保存する。"""

    def append_failure(self, *, run_dir: Path, record: RunFailureRecord) -> None:
        failure_dir = run_dir / _FAILURES_DIR
        failure_dir.mkdir(parents=True, exist_ok=True)
        payload = record.to_payload()
        self._append_jsonl(failure_dir / _FAILURES_JSONL, payload, snapshot_path=failure_dir / _FAILURES_JSON)

    def materialize_snapshots(self, run_dir: Path) -> None:
        """局の投入終了後、互換用の JSON snapshot を一度生成する。"""
        failure_dir = run_dir / _FAILURES_DIR
        jsonl_path = failure_dir / _FAILURES_JSONL
        if not jsonl_path.exists():
            return
        records = self._load_jsonl(jsonl_path, strict=True)
        self._write_snapshot(failure_dir / _FAILURES_JSON, records)
        startup_records = [record for record in records if _is_startup_failure_payload(record)]
        if startup_records:
            self._write_snapshot(failure_dir / _STARTUP_FAILURE_JSON, startup_records)

    async def materialize_snapshots_async(self, run_dir: Path) -> None:
        """取消中でも snapshot worker の完了を待ってから戻る。"""
        task = asyncio.create_task(asyncio.to_thread(self.materialize_snapshots, run_dir))
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = exc
            except Exception:
                # The worker has failed. Preserve an earlier caller cancellation below.
                break
        if cancellation is not None:
            try:
                await task
            except Exception as exc:
                logger.warning("Failure snapshot worker failed during cancellation: %s", exc, exc_info=True)
            raise cancellation
        await task

    def load_failures(self, run_dir: Path) -> list[JsonObject]:
        failure_dir = run_dir / _FAILURES_DIR
        jsonl_path = failure_dir / _FAILURES_JSONL
        if jsonl_path.exists():
            return self._load_jsonl(jsonl_path)
        snapshot_path = failure_dir / _FAILURES_JSON
        if snapshot_path.exists():
            loaded = self._load_snapshot(snapshot_path)
            if loaded is not None:
                return loaded
        return []

    @staticmethod
    def build_record_from_exception(
        *,
        exc: BaseException,
        game_id: str | None,
        scheduled_black_engine: str | None,
        scheduled_white_engine: str | None,
        fallback_phase: FailurePhase = "unknown",
        log_artifact_path: str | None = None,
    ) -> RunFailureRecord:
        phase = fallback_phase
        engine = None
        diagnostic: JsonObject | None = None
        diagnostic_payload = getattr(exc, "diagnostic_payload", None)
        if callable(diagnostic_payload):
            phase = coerce_failure_phase(coerce_str(getattr(exc, "failure_phase", None)) or fallback_phase)
            engine = coerce_str(getattr(exc, "engine_name", None))
            raw_diagnostic = diagnostic_payload()
            if isinstance(raw_diagnostic, Mapping):
                diagnostic = coerce_json_object_serialized(raw_diagnostic, field_name="diagnostic")
        return RunFailureRecord(
            game_id=game_id,
            scheduled_black_engine=scheduled_black_engine,
            scheduled_white_engine=scheduled_white_engine,
            failure_phase=phase,
            exception_class=type(exc).__name__,
            short_message=str(exc),
            engine=engine,
            log_artifact_path=log_artifact_path,
            diagnostic=diagnostic,
        )

    @staticmethod
    def failure_counts_by_phase(records: Iterable[Mapping[str, JsonValue]]) -> JsonObject:
        counts: dict[str, int] = {}
        for record in records:
            phase = coerce_failure_phase(coerce_str(record.get("failure_phase")))
            counts[phase] = counts.get(phase, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _append_jsonl(path: Path, payload: JsonObject, *, snapshot_path: Path) -> None:
        try:
            line = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            with _APPEND_LOCK:
                if not path.exists() and snapshot_path.exists():
                    loaded = RunFailureRecordService._load_snapshot(snapshot_path)
                    if loaded is None:
                        raise ValueError(f"Cannot migrate invalid run failure snapshot: {snapshot_path}")
                    RunFailureRecordService._install_legacy_jsonl(path, loaded)
                with path.open("a+b") as handle:
                    RunFailureRecordService._truncate_torn_tail(handle, path)
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            logger.exception("Failed to append run failure record to %s: %s", path, exc)

    @staticmethod
    def _install_legacy_jsonl(path: Path, records: list[JsonObject]) -> None:
        """Install the legacy snapshot as a complete JSONL prefix before appending."""
        temporary = path.with_name(f".{path.name}.migrate.tmp")
        with temporary.open("wb") as handle:
            for record in records:
                handle.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return  # Windows does not support opening directories for fsync.
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            logger.debug("Directory fsync failed for %s: %s", path.parent, exc)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _truncate_torn_tail(handle: BinaryIO, path: Path) -> None:
        handle.seek(0, os.SEEK_END)
        cursor = handle.tell()
        if cursor == 0:
            return
        handle.seek(cursor - 1)
        if handle.read(1) == b"\n":
            return
        while cursor:
            start = max(0, cursor - 4096)
            handle.seek(start)
            chunk = handle.read(cursor - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                handle.truncate(start + newline + 1)
                break
            cursor = start
        else:
            handle.truncate(0)
        logger.warning("Truncated incomplete run failure JSONL tail: %s", path)

    @staticmethod
    def _write_snapshot(path: Path, records: list[JsonObject]) -> None:
        payload: JsonObject = {
            "schema_version": 1,
            "failures": records,
        }
        write_json_atomic(path, payload)

    @staticmethod
    def _load_snapshot(path: Path) -> list[JsonObject] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed to load run failure snapshot from %s: %s", path, exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        failures = raw.get("failures")
        if not isinstance(failures, list):
            return None
        records: list[JsonObject] = []
        for item in failures:
            if isinstance(item, Mapping):
                records.append(coerce_json_object_serialized(item, field_name="failures[]"))
        return records

    @staticmethod
    def _load_jsonl(path: Path, *, strict: bool = False) -> list[JsonObject]:
        if not path.exists():
            return []
        records: list[JsonObject] = []
        try:
            contents = path.read_bytes()
        except OSError as exc:
            if strict:
                raise
            logger.debug("Failed to load run failure jsonl from %s: %s", path, exc)
            return []
        # A crash can leave the last UTF-8 character incomplete. Only newline-terminated
        # records are committed; decode each of those independently.
        contents = contents[: contents.rfind(b"\n") + 1]
        for line in contents.splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if strict:
                    raise ValueError(f"Invalid run failure jsonl line in {path}") from exc
                logger.debug("Skipping invalid run failure jsonl line from %s", path)
                continue
            if isinstance(raw, Mapping):
                records.append(coerce_json_object_serialized(raw, field_name="run_failures.jsonl"))
        return records


def _is_startup_failure_payload(record: Mapping[str, JsonValue]) -> bool:
    phase = coerce_str(record.get("failure_phase"))
    return phase in {"engine_start", "isready"}


__all__ = ["RunFailureRecordService"]
